"""PrintBot camera: the Bambu H2S live view as a WebRTC-only camera.

Why this exists
---------------
The H2S streams 1080p H.264 at 30 fps over RTSPS (port 322), but its RTSP server
only behaves with one viewer. When a second client connects, the first one's RTP
timestamps jump by about 210 s, and after a client leaves the others stall for
4-6 s at a time (measured 2026-11-17 on firmware 01.03.00.00).

Home Assistant's frontend opens two connections for an ordinary stream camera:
it starts the HLS player (HA's own stream worker) and, hidden, a WebRTC player
(go2rtc) at the same time, then keeps whichever works. With this printer the two
knock each other over: HLS dies on "Timestamp discontinuity", go2rtc times out
after 5 s without data, and the view falls back to slow, choppy stills.

This camera tells the frontend it speaks WebRTC natively, so the frontend only
starts the WebRTC player. It hands the WebRTC offer, ICE candidates and snapshots
to Home Assistant's built-in go2rtc. go2rtc keeps a single connection to the
printer and fans it out to every viewer and to snapshots.

Configuration (configuration.yaml)::

    camera:
      - platform: printbot_camera
        name: PrintBot Live
        unique_id: printbot_live
        source: camera.printbot_camera          # ha-bambulab's camera supplies the URL
        # or, instead of source:
        # url: !secret printbot_camera_url     # rtsps://bblp:ACCESS_CODE@PRINTER_IP:322/streaming/live/1
        # rtsp_timeout: 15                      # seconds of silence before go2rtc reconnects
"""

from __future__ import annotations

import logging

import voluptuous as vol
from webrtc_models import RTCIceCandidateInit

from homeassistant.components.camera import (
    PLATFORM_SCHEMA as CAMERA_PLATFORM_SCHEMA,
    Camera,
    CameraEntityFeature,
    async_get_stream_source,
)
from homeassistant.components.camera.webrtc import (
    DATA_WEBRTC_PROVIDERS,
    CameraWebRTCProvider,
    WebRTCError,
    WebRTCSendMessage,
)
from homeassistant.const import CONF_NAME, CONF_UNIQUE_ID, CONF_URL
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

_LOGGER = logging.getLogger(__name__)

CONF_SOURCE = "source"
CONF_RTSP_TIMEOUT = "rtsp_timeout"
GO2RTC_DOMAIN = "go2rtc"
RTSP_SCHEMES = ("rtsp://", "rtsps://", "rtspx://")

PLATFORM_SCHEMA = vol.All(
    CAMERA_PLATFORM_SCHEMA.extend(
        {
            vol.Exclusive(CONF_URL, "stream"): cv.string,
            vol.Exclusive(CONF_SOURCE, "stream"): cv.entity_domain("camera"),
            vol.Optional(CONF_NAME, default="PrintBot Live"): cv.string,
            vol.Optional(CONF_UNIQUE_ID): cv.string,
            vol.Optional(CONF_RTSP_TIMEOUT, default=15): vol.All(
                vol.Coerce(int), vol.Range(min=5, max=120)
            ),
        }
    ),
    cv.has_at_least_one_key(CONF_URL, CONF_SOURCE),
)


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Set up the PrintBot camera from configuration.yaml."""
    async_add_entities(
        [
            PrintbotCamera(
                name=config[CONF_NAME],
                unique_id=config.get(CONF_UNIQUE_ID),
                url=config.get(CONF_URL),
                source=config.get(CONF_SOURCE),
                rtsp_timeout=config[CONF_RTSP_TIMEOUT],
            )
        ]
    )


class PrintbotCamera(Camera):
    """A camera that only offers WebRTC, served by Home Assistant's go2rtc."""

    _attr_brand = "Bambu Lab"
    _attr_icon = "mdi:printer-3d"
    _attr_should_poll = False
    _attr_supported_features = CameraEntityFeature.STREAM

    def __init__(
        self,
        name: str,
        unique_id: str | None,
        url: str | None,
        source: str | None,
        rtsp_timeout: int,
    ) -> None:
        """Initialize the camera."""
        super().__init__()
        self._attr_name = name
        self._attr_unique_id = unique_id
        self._url = url
        self._source = source
        self._rtsp_timeout = rtsp_timeout

    async def stream_source(self) -> str | None:
        """Return the printer's RTSPS URL for go2rtc (never logged: it holds the access code)."""
        url = self._url
        if self._source:
            try:
                url = await async_get_stream_source(self.hass, self._source)
            except HomeAssistantError as err:
                _LOGGER.warning("Source camera %s isn't available: %s", self._source, err)
                return None
        if not url:
            return None
        if url.startswith(RTSP_SCHEMES) and "#" not in url:
            # go2rtc reconnects after 5 s without data by default; give the printer longer.
            url += f"#timeout={self._rtsp_timeout}"
        return url

    def _go2rtc(self) -> CameraWebRTCProvider:
        for provider in self.hass.data.get(DATA_WEBRTC_PROVIDERS, ()):
            if provider.domain == GO2RTC_DOMAIN:
                return provider
        raise HomeAssistantError("Home Assistant's go2rtc isn't running")

    # Overriding async_handle_async_webrtc_offer makes Home Assistant treat this
    # camera as native WebRTC, so the frontend offers WebRTC only (no HLS).
    async def async_handle_async_webrtc_offer(
        self, offer_sdp: str, session_id: str, send_message: WebRTCSendMessage
    ) -> None:
        """Pass the browser's WebRTC offer to go2rtc."""
        try:
            provider = self._go2rtc()
        except HomeAssistantError as err:
            send_message(WebRTCError("printbot_camera_no_go2rtc", str(err)))
            return
        await provider.async_handle_async_webrtc_offer(
            self, offer_sdp, session_id, send_message
        )

    async def async_on_webrtc_candidate(
        self, session_id: str, candidate: RTCIceCandidateInit
    ) -> None:
        """Pass an ICE candidate to go2rtc."""
        await self._go2rtc().async_on_webrtc_candidate(session_id, candidate)

    @callback
    def close_webrtc_session(self, session_id: str) -> None:
        """Close the go2rtc session when the viewer goes away."""
        try:
            self._go2rtc().async_close_session(session_id)
        except HomeAssistantError:
            pass

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return a snapshot from go2rtc, sharing its one connection to the printer."""
        return await self._go2rtc().async_get_image(self, width=width, height=height)
