# IP Camera Plugin for ChitUI

Stream IP cameras in ChitUI using RTSP, HTTP/MJPEG, or other protocols. This plugin leverages OpenCV with GStreamer and FFmpeg support for robust camera streaming.

## Features

- **Multiple Camera Support**: Add and manage multiple IP cameras
- **Protocol Support**: RTSP, HTTP/MJPEG, and auto-detection
- **Live Streaming**: Real-time MJPEG streaming to the web interface
- **Easy Configuration**: User-friendly settings interface
- **Tab Integration**: Dedicated "IP Camera" tab in the main interface
- **Fullscreen Mode**: Click any camera stream to view in fullscreen
- **Status Indicators**: Visual indicators for active/inactive cameras

## Requirements

- Python 3.7+
- OpenCV (opencv-python >= 4.5.0)
- FFmpeg (system package)
- GStreamer (system package, optional but recommended)

## Installation

The plugin is already included in ChitUI. Dependencies will be installed automatically when you enable the plugin.

To manually install dependencies:

```bash
pip install opencv-python>=4.5.0
```

For optimal performance, install FFmpeg and GStreamer:

```bash
# Raspberry Pi / Debian / Ubuntu
sudo apt-get install ffmpeg libgstreamer1.0-0 gstreamer1.0-plugins-base gstreamer1.0-plugins-good

# Or for full GStreamer support:
sudo apt-get install gstreamer1.0-tools gstreamer1.0-plugins-bad gstreamer1.0-plugins-ugly
```

## Configuration

### Adding a Camera

1. Navigate to the **IP Camera** tab in ChitUI
2. Click **Configure Cameras**
3. Fill in the camera details:
   - **Camera Name**: A friendly name for your camera (e.g., "Xiaomi Camera 2K")
   - **Protocol**: Select RTSP, HTTP/MJPEG, or Auto-detect
   - **Stream URL**: The full URL to your camera stream

4. Click **Add Camera**

### Camera URL Examples

#### Xiaomi Mi Camera 2K
```
rtsp://192.168.1.100:554/
```

#### Generic RTSP with Authentication
```
rtsp://username:password@192.168.1.100:554/stream
```

#### HTTP MJPEG Stream
```
http://192.168.1.100:8080/video
```

#### Wyze Cam with RTSP Firmware
```
rtsp://192.168.1.100:554/live
```

### Finding Your Camera URL

Different camera brands use different URL formats. Common paths:

- `/` or `/stream` - Xiaomi, some IP cameras
- `/live` - Wyze, some home security cameras
- `/video` - HTTP MJPEG cameras
- `/h264` or `/mjpeg` - Generic IP cameras
- `/onvif` - ONVIF-compatible cameras

Check your camera's documentation or use an RTSP scanner tool.

## Usage

### Starting a Camera Stream

1. Go to the **IP Camera** tab
2. Click the **Start** button on any configured camera
3. The stream will appear in the camera card
4. Click the stream or the fullscreen button to view in fullscreen mode

### Stopping a Camera Stream

Click the **Stop** button on any active camera to stop streaming.

### Editing a Camera

1. Click **Configure Cameras**
2. Click the **Edit** (pencil) icon next to the camera you want to edit
3. Make your changes and click **Save Changes**

### Deleting a Camera

1. Click **Configure Cameras**
2. Click the **Delete** (trash) icon next to the camera you want to remove
3. Confirm the deletion

## Troubleshooting

### Camera Won't Connect

1. **Check Network**: Ensure the camera is on the same network as ChitUI
2. **Verify URL**: Test the RTSP URL with VLC or another media player
3. **Check Authentication**: Make sure username/password are included in the URL if required
4. **Protocol**: Try switching between RTSP and HTTP/MJPEG protocols
5. **Firewall**: Check if the camera port (usually 554 for RTSP) is accessible

### Stream is Laggy

- **Buffer Settings**: The plugin uses a buffer size of 1 to reduce latency
- **Network**: Check your network connection quality
- **Resolution**: Some cameras allow you to use lower resolution streams (e.g., `/stream2` instead of `/stream`)

### OpenCV Errors

If you see "OpenCV not installed" errors:

```bash
pip install opencv-python
```

For better codec support:

```bash
sudo apt-get install libavcodec-dev libavformat-dev libswscale-dev
pip uninstall opencv-python
pip install opencv-python --no-binary opencv-python
```

## Technical Details

### Architecture

- **Backend**: Python Flask with threading for camera capture
- **Streaming**: MJPEG over HTTP (multipart/x-mixed-replace)
- **Video Capture**: OpenCV with FFmpeg/GStreamer backends
- **Frame Processing**: Resize to max 1280px width, JPEG quality 75%
- **Latency Reduction**: Frame skipping and minimal buffering

### Configuration Storage

Camera configurations are stored in:
```
~/.chitui/ip_camera_config.json
```

### API Endpoints

- `GET /plugin/ip_camera/cameras` - List all cameras
- `POST /plugin/ip_camera/camera/<id>/start` - Start camera stream
- `POST /plugin/ip_camera/camera/<id>/stop` - Stop camera stream
- `GET /plugin/ip_camera/camera/<id>/video` - MJPEG stream
- `GET/POST /plugin/ip_camera/config` - Get/update camera configurations

## Tested Cameras

- ✅ Xiaomi Mi Camera 2K
- ✅ Wyze Cam (with RTSP firmware)
- ✅ Generic ONVIF cameras
- ✅ HTTP MJPEG cameras

## Support

For issues or feature requests, please visit the ChitUI repository.

## License

This plugin is part of ChitUI and follows the same license.

## Version History

### 1.0.0 (2025-01-21)
- Initial release
- Multiple camera support
- RTSP and HTTP/MJPEG protocols
- Tab-based UI with fullscreen mode
- Configuration management
