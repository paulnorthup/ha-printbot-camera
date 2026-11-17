# PrintBot camera

A Home Assistant camera that plays a Bambu Lab H2S's live view smoothly. It
serves the printer's own 1080p / 30 fps stream over WebRTC through Home
Assistant's built-in go2rtc, using a single connection to the printer.

## Why

The H2S streams H.264 over RTSPS on port 322, but its RTSP server only behaves
with one viewer. When a second client connects, the first one's RTP timestamps
jump by about 210 s, and after a client leaves the others stall for 4–6 s
(measured on firmware 01.03.00.00).

For an ordinary stream camera, Home Assistant's dashboard opens two connections
at once: the HLS player and, hidden, a WebRTC player through go2rtc. With this
printer they knock each other over:

- HLS fails with `Timestamp discontinuity detected`.
- go2rtc logs `i/o timeout`.
- The view drops to slow, choppy stills.

That's what happens with the camera from
[ha-bambulab](https://github.com/greghesp/ha-bambulab).

This camera presents itself as native WebRTC, so the dashboard starts only the
WebRTC player. It passes the offer, ICE candidates and snapshots to Home
Assistant's go2rtc, which keeps one connection to the printer for every viewer.
It also raises go2rtc's read timeout from 5 s to 15 s, so a short stall doesn't
trigger a reconnect.

## Requirements

- Home Assistant 2026.11 or later, with the built-in `go2rtc` integration (part
  of `default_config`).
- The printer's LAN Mode Liveview on, so it reports an RTSPS URL.

## Install

1. In **Marketplace**, add this repository as a custom repository (type
   *Integration*) and download it.
2. Add this to `configuration.yaml`, then restart Home Assistant:

   ```yaml
   camera:
     - platform: printbot_camera
       name: PrintBot Live
       unique_id: printbot_live
       source: camera.printbot_camera   # the camera entity from ha-bambulab
   ```

   Without ha-bambulab, give the URL instead of `source:` and keep it in
   `secrets.yaml`:

   ```yaml
       url: !secret printbot_camera_url
   ```

   ```yaml
   # secrets.yaml
   printbot_camera_url: rtsps://bblp:ACCESS_CODE@PRINTER_IP:322/streaming/live/1
   ```

   Optional: `rtsp_timeout: 15` sets how many seconds go2rtc waits for video
   before reconnecting (5–120).

## After installing

- Use the new camera in dashboards.
- Hide the ha-bambulab camera; don't disable it if it's your `source:`.
- Avoid watching in Bambu Studio and Home Assistant at the same time. Each one
  adds a viewer, and the printer glitches with more than one.
