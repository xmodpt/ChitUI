<div align="center">
  <img src="img/logo.png" alt="ChitUI Logo" width="200"/>

  # ChitUI

  Web-based interface for controlling Chitu-based 3D printers with real-time monitoring, file management, and plugin support.
</div>

## Release Notes

**Version 2.1**
- Minor bug fixes
- Added thumbnail auto generation in file manager
- "Settings" button moved to side bar for better user experience

## Features

- **Automatic Printer Discovery** - UDP broadcast discovery for network printers
- **Real-time Monitoring** - WebSocket-based status updates
- **File Management** - Upload and manage files via USB Gadget or Network mode
- **Automatic Thumbnail Extraction** - Auto-generate and display thumbnails from .ctb and .goo files on upload
- **Print Control** - Start, pause, resume, and stop prints remotely
- **Temperature Control** - Monitor and adjust printer temperatures
- **Plugin System** - Extensible architecture with built-in plugins:
  - GPIO Relay Control - Control relays for lights, fans, etc.
  - IP Camera Streaming - Stream RTSP/HTTP cameras
  - Raspberry Pi Stats - System monitoring
  - Terminal - Direct printer communication
- **User Authentication** - Password protection and session management
- **USB Gadget Mode** - Raspberry Pi appears as USB drive to printer

## Screenshots

<div align="center">
  <img src="img/main.png" alt="ChitUI Main Interface" width="800"/>
  <p><em>Main Interface - Printer Monitoring and Control</em></p>

  <img src="img/thumbs.png" alt="Automatic Thumbnail Extraction" width="800"/>
  <p><em>Automatic Thumbnail Extraction from Print Files</em></p>
</div>

## Installation

### Recommended Installation Path

ChitUI should be installed to `~/ChitUI` for best compatibility:

```bash
cd ~
git clone https://github.com/xmodpt/ChitUI.git ChitUI
cd ChitUI
./install.sh
```

### Quick Start

1. **Clone the repository:**
   ```bash
   cd ~
   git clone https://github.com/xmodpt/ChitUI.git ChitUI
   cd ChitUI
   ```

2. **Run the installer:**
   ```bash
   ./install.sh
   ```

   The installer will guide you through:
   - Installing Python dependencies
   - Setting up configuration directory (`~/.chitui`)
   - Optional: Virtual USB Gadget setup
   - Optional: Auto-start service installation

3. **Access the web interface:**
   - Local: `http://localhost:8080`
   - Network: `http://<your-pi-ip>:8080`

4. **Default login:**
   - Password: `admin`
   - You'll be prompted to change it on first login

## Requirements

- **Hardware:**
  - Raspberry Pi (any model)
  - For USB Gadget: Pi Zero, Zero W, Zero 2 W, or Pi 4/5 with OTG support

- **Software:**
  - Raspberry Pi OS (Debian-based)
  - Python 3.7+
  - Git (recommended)

## Usage

### Running ChitUI

**If you installed as a service:**
```bash
sudo systemctl status chitui    # Check status
sudo systemctl restart chitui   # Restart
journalctl -u chitui -f         # View logs
```

**If running manually:**
```bash
cd ~/ChitUI
./run.sh
```

Or directly:
```bash
cd ~/ChitUI
python3 main.py
```

### Configuration

Configuration files are stored in `~/.chitui/`:
- `chitui_settings.json` - Application settings, printer list
- `plugin_settings.json` - Plugin enable/disable states
- `gpio_relay_config.json` - GPIO relay configuration
- `ip_camera_config.json` - IP camera settings
- `service.log` - Service logs (if installed as service)

## Virtual USB Gadget

The Virtual USB Gadget makes your Raspberry Pi appear as a USB flash drive when connected to your 3D printer.

**Setup:**
1. Run the installer and choose "Yes" for USB Gadget setup
2. Reboot your Pi
3. Connect Pi's OTG USB port to printer's USB port
4. Files uploaded via ChitUI will appear on the printer

**Manual setup:**
```bash
cd ~/ChitUI
sudo ./scripts/virtual_usb_gadget_fixed.sh
```

**Testing:**
```bash
cd ~/ChitUI
bash ./scripts/check_usb_gadget.sh
```

## Plugins

### Managing Plugins

1. Navigate to **Settings** → **Plugins**
2. Enable/disable plugins as needed
3. Upload custom plugins as `.zip` files
4. Refresh Python packages if needed

### Built-in Plugins

- **GPIO Relay Control** - Control up to 3 GPIO relays from the toolbar
- **IP Camera** - Stream RTSP/MJPEG cameras in a dedicated tab
- **Raspberry Pi Stats** - View CPU, memory, disk, and network stats
- **Terminal** - Direct terminal access to printer communication

See individual plugin README files in `plugins/` directory for details.

## Updating

```bash
cd ~/ChitUI
git pull
sudo systemctl restart chitui  # If running as service
```

## Troubleshooting

### Service won't start
```bash
sudo systemctl status chitui
journalctl -u chitui -n 50
```

### USB Gadget not working
```bash
cd ~/ChitUI
bash ./scripts/check_usb_gadget.sh
```

### Port already in use
```bash
sudo lsof -i :8080
# Kill the process using port 8080 if needed
```

### Printer not discovered
- Ensure printer and Pi are on the same network
- Check printer's network settings
- Try manual printer addition in Settings

## Documentation

- `scripts/README.md` - Script documentation
- `plugins/*/README.md` - Plugin-specific guides
- `plugins/rpi_stats/INSTALL.md` - RPi Stats installation

## Credits

This project is based on the original [ChitUI](https://github.com/jangrewe/ChitUI) proof of concept by **Jan Grewe**.

## Technology Stack

**Backend:**
- Python 3.11
- Flask (Web framework)
- Flask-SocketIO (WebSocket support)
- Loguru (Logging)

**Frontend:**
- Bootstrap 5
- Socket.IO Client
- Bootstrap Icons
- Vanilla JavaScript

## Version

**Current Version:** 2.1

## License

See LICENSE file for details.

## Support

For issues, questions, or contributions, please visit the project repository.
