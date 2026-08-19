# modules/system.py
import os
import psutil
import platform
import subprocess
import threading
import time
import logging
import json
from datetime import datetime
import importlib.metadata

logger = logging.getLogger(__name__)


class SystemInfoManager:
    def __init__(self, config, socketio, app_version):
        self.config = config
        self.socketio = socketio
        self.app_version = app_version
        self.dhcp_clients_cache = []
        self.DHCP_REFRESH_INTERVAL = 60  # seconds

        self._start_background_tasks()

    def _start_background_tasks(self):
        """Start background refresh threads"""
        threading.Thread(target=self._dhcp_refresh_loop, daemon=True).start()

    def _dhcp_refresh_loop(self):
        """Background task to refresh DHCP clients"""
        while True:
            time.sleep(self.DHCP_REFRESH_INTERVAL)
            try:
                self.get_dhcp_clients()
                self.socketio.emit('dhcp_update', {'dhcp_clients': self.dhcp_clients_cache})
            except Exception as e:
                logger.debug(f"DHCP refresh failed: {e}")

    # ====================== DHCP ======================

    def get_dhcp_clients(self):
        """Parse DHCP leases (Pi-hole first, then legacy system dnsmasq)."""
        try:
            clients = []
            lease_files = (
                "/etc/pihole/dhcp.leases",
                "/var/lib/misc/dnsmasq.leases",
            )
            for lease_file in lease_files:
                if not os.path.exists(lease_file):
                    continue
                with open(lease_file, "r") as f:
                    for line in f:
                        parts = line.strip().split()
                        if len(parts) >= 5:
                            try:
                                expiry = datetime.fromtimestamp(int(parts[0])).strftime("%H:%M")
                            except (ValueError, OSError):
                                expiry = "?"
                            clients.append(
                                {
                                    "mac": parts[1],
                                    "ip": parts[2],
                                    "name": parts[3] if parts[3] != "*" else "Unknown",
                                    "lease_expiry": expiry,
                                }
                            )
                if clients:
                    break

            self.dhcp_clients_cache = sorted(clients, key=lambda x: x["name"].lower())
            return self.dhcp_clients_cache
        except Exception as e:
            logger.error(f"Failed to read DHCP leases: {e}")
            return []

    # ====================== SYSTEM INFO ======================

    def get_system_info(self):
        """Return comprehensive system information for diagnostics page"""
        try:
            data = {
                "timestamp": datetime.now().isoformat(),
                "hostname": platform.node(),
                "model": self._get_model(),
                "os": f"{platform.system()} {platform.release()}",
                "kernel": platform.release(),
                "python_version": platform.python_version(),
                "flask_version": self._get_flask_version(),
                "app_version": self.app_version,                    # Fixed
                "uptime": self._get_uptime(),
                "boot_time": datetime.fromtimestamp(psutil.boot_time()).strftime("%Y-%m-%d %H:%M:%S"),

                "cpu_model": self._get_cpu_model(),
                "cpu_cores": psutil.cpu_count(logical=False),
                "cpu_threads": psutil.cpu_count(logical=True),
                "cpu_temp": self._get_cpu_temp(),
                "cpu_percent": round(psutil.cpu_percent(interval=0.4), 1),
                "load_avg": self._get_load_avg(),

                "memory_total": round(psutil.virtual_memory().total / (1024**2)),
                "memory_used": round(psutil.virtual_memory().used / (1024**2)),
                "memory_percent": psutil.virtual_memory().percent,

                "disk_total": round(psutil.disk_usage('/').total / (1024**3), 1),
                "disk_used": round(psutil.disk_usage('/').used / (1024**3), 1),
                "disk_percent": psutil.disk_usage('/').percent,

                "throttling_status": self._get_throttling_status(),
                "throttling_raw": self._get_throttling_raw(),
                "throttling_color": self._get_throttling_color(),

                "network_details": self._get_network_details(),
                "dhcp_clients": self.dhcp_clients_cache,
                "dhcp_range": self._get_dhcp_range(),               # Added

                "connected_clients": self._get_connected_clients(),
                "process_count": len(psutil.pids()),
                "top_processes": self._get_top_processes(),

                # WiFi client connection status (for diag WiFi tile + Network Details)
                "current_wifi": self.get_current_wifi(),
            }

            return data

        except Exception as e:
            logger.error(f"Error gathering system info: {e}")
            return {"error": str(e)}

    # ====================== HELPER METHODS ======================

    def _get_model(self):
        try:
            with open('/proc/device-tree/model') as f:
                return f.read().strip()
        except:
            return "Unknown"

    def _get_cpu_model(self):
        try:
            with open('/proc/cpuinfo') as f:
                for line in f:
                    if 'CPU part' in line:
                        cpu_part = line.split(':', 1)[1].strip()
                        if cpu_part == '0xd0b':
                            return "Broadcom BCM2712 (4× Cortex-A76)"
                        return f"ARM CPU (part 0x{cpu_part})"
            return "Unknown"
        except:
            return "Unknown"

    def _get_cpu_temp(self):
        try:
            with open('/sys/class/thermal/thermal_zone0/temp') as f:
                return round(int(f.read()) / 1000, 1)
        except:
            return None

    def _get_load_avg(self):
        try:
            return " ".join([f"{x:.2f}" for x in os.getloadavg()])
        except:
            return None

    def _get_uptime(self):
        try:
            return str(datetime.now() - datetime.fromtimestamp(psutil.boot_time()))
        except:
            return "Unknown"

    def _get_flask_version(self):
        try:
            return importlib.metadata.version("flask")
        except:
            return "Unknown"

    def _get_throttling_status(self):
        try:
            result = subprocess.check_output(['vcgencmd', 'get_throttled'], 
                                           stderr=subprocess.STDOUT, timeout=3).decode().strip()
            if '=' in result:
                raw = result.split('=')[1].strip()
                return "Normal ✓" if raw == '0x0' else "Throttled ⚠️"
            return "Unknown"
        except:
            return "vcgencmd unavailable"

    def _get_throttling_raw(self):
        try:
            result = subprocess.check_output(['vcgencmd', 'get_throttled'], 
                                           stderr=subprocess.STDOUT, timeout=3).decode().strip()
            return result.split('=')[1].strip() if '=' in result else "N/A"
        except:
            return "N/A"

    def _get_throttling_color(self):
        try:
            result = subprocess.check_output(['vcgencmd', 'get_throttled'], 
                                           stderr=subprocess.STDOUT, timeout=3).decode().strip()
            return "#4ade80" if '=' in result and result.split('=')[1].strip() == '0x0' else "#fbbf24"
        except:
            return "#94a3b8"

    def _get_network_details(self):
        details = []

        # Show all active interfaces
        for iface in ['eth0', 'wlan0', 'usb0', 'rndis0', 'enp0s3']:
            try:
                output = subprocess.check_output(
                    f"ip addr show {iface} 2>/dev/null", shell=True, timeout=2
                ).decode()
                for line in output.splitlines():
                    if 'inet ' in line and '127.' not in line:
                        ip = line.split()[1].split('/')[0]
                        status = "UP" if "state UP" in output else "DOWN"
                        details.append(f"{iface}: {ip} ({status})")
            except:
                pass

        # ==================== UPSTREAM GATEWAY ====================
        gateway = "Not detected"
        try:
            # Much more reliable: use 'ip route get' to an external IP
            output = subprocess.check_output(
                "ip route get 8.8.8.8", shell=True, timeout=3
            ).decode().strip()

            for line in output.splitlines():
                if 'via' in line:
                    parts = line.split()
                    try:
                        # Find gateway IP and dev
                        gw_ip = None
                        via_iface = None
                        
                        for i, p in enumerate(parts):
                            if p == 'via' and i + 1 < len(parts):
                                gw_ip = parts[i + 1]
                            elif p == 'dev' and i + 1 < len(parts):
                                via_iface = parts[i + 1]
                        
                        if gw_ip and via_iface:
                            gateway = f"{gw_ip} (via {via_iface})"
                            break
                    except:
                        continue

        except Exception as e:
            logger.debug(f"Gateway detection failed: {e}")
            # Fallback to original method
            try:
                output = subprocess.check_output("ip route show default", shell=True, timeout=3).decode()
                for line in output.splitlines():
                    if 'default via' in line:
                        parts = line.split()
                        gw_ip = parts[2]
                        via_iface = parts[-1] if len(parts) > 3 else "unknown"
                        
                        # Improved index-to-name mapping
                        if via_iface.isdigit():
                            via_iface = self._get_interface_name_from_index(via_iface)
                        
                        gateway = f"{gw_ip} (via {via_iface})"
                        break
            except:
                pass

        details.append(f"Gateway: {gateway}")

        # ==================== DNS SERVERS ====================
        dns_servers = []
        try:
            for conf_path in ['/etc/dnsmasq.conf', '/etc/dnsmasq/dnsmasq.conf']:
                if os.path.exists(conf_path):
                    with open(conf_path, 'r') as f:
                        for line in f:
                            line = line.strip()
                            if line.startswith('server=') and not line.startswith('server=/'):
                                dns = line.split('=')[1].strip()
                                if dns not in dns_servers and dns != '127.0.0.1':
                                    dns_servers.append(dns)
                    if dns_servers:
                        break
        except:
            pass

        if dns_servers:
            for i, dns in enumerate(dns_servers, 1):
                label = f"DNS {i} (Primary)" if i == 1 else f"DNS {i}"
                details.append(f"{label}: {dns}")
        else:
            details.append("DNS: Not detected")

        # WAN IP
        try:
            public_ip = subprocess.check_output(
                "curl -s --max-time 6 https://api.ipify.org || echo 'N/A'",
                shell=True, timeout=8
            ).decode().strip()
            details.append(f"WAN IP: {public_ip}" if public_ip != 'N/A' else "WAN IP: unavailable")
        except:
            details.append("WAN IP: unavailable")

        # Traffic
        try:
            stats = psutil.net_io_counters()
            details.append(f"Total Sent: {stats.bytes_sent / (1024*1024):.1f} MB")
            details.append(f"Total Received: {stats.bytes_recv / (1024*1024):.1f} MB")
        except:
            pass

        return details

    def _get_dhcp_range(self):
        """DHCP pool: Pi-hole static conf or legacy dnsmasq."""
        try:
            paths = [
                "/etc/dnsmasq.d/99-sccs-static-dhcp.conf",
                "/etc/dnsmasq.d/sccs-lan.conf",
                "/etc/dnsmasq.conf",
                "/etc/dnsmasq/dnsmasq.conf",
            ]
            for conf_path in paths:
                if not os.path.exists(conf_path):
                    continue
                with open(conf_path, "r") as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith("dhcp-range="):
                            parts = line.split("=", 1)[1].split(",")
                            if len(parts) >= 2:
                                start = parts[0].strip()
                                end = parts[1].strip()
                                return f"{start} — {end}"
            # Pi-hole v6 defaults from installer
            return "10.10.10.50 — 10.10.10.200 (Pi-hole)"
        except Exception as e:
            logger.debug(f"Could not read DHCP range: {e}")
            return "Unable to read DHCP range"

    def _get_top_processes(self, limit=6):
        try:
            processes = []
            for proc in sorted(psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_percent']), 
                             key=lambda p: (p.info['cpu_percent'] or 0), reverse=True)[:limit]:
                processes.append({
                    'name': proc.info['name'][:28],
                    'cpu': round(proc.info['cpu_percent'] or 0, 1),
                    'mem': round(proc.info['memory_percent'] or 0, 1)
                })
            return processes
        except:
            return []

    def _get_connected_clients(self):
        try:
            if hasattr(self.socketio, 'server') and hasattr(self.socketio.server, 'manager'):
                return len(self.socketio.server.manager.rooms.get('/', {}))
            return 0
        except:
            return 0
            
    def _get_interface_name_from_index(self, ifindex: str) -> str:
        """Convert interface index (e.g. '600') to name (wlan0, usb0, etc.)"""
        try:
            output = subprocess.check_output("ip link show", shell=True, timeout=2).decode()
            for line in output.splitlines():
                if ifindex + ':' in line:
                    name = line.split(':', 2)[1].strip()
                    return name
        except:
            pass
        return ifindex

    # ====================== ITERATION 2 HELPERS (Network tile) ======================

    # Note: some ping logic lives in app.py for the tile broadcaster.
    # If more sophisticated ping handling is needed later it can be moved here.

    # ====================== Signal Quality Helpers ======================

    def _get_current_signal_quality(self, iface: str | None) -> str | None:
        """Return a human string like '92%' for the WAN quality where possible (no parenthetical description)."""
        if not iface:
            return None

        # WiFi client interfaces (house WiFi, campsite WiFi tether, future Starlink Go WiFi)
        if self._looks_like_wireless(iface):
            return self._get_wifi_signal_quality(iface)

        # USB / RNDIS tethering from a phone — try to read the phone's cellular signal
        if iface.startswith(("usb", "rndis")):
            return self._get_usb_cellular_signal_quality()

        # Direct Ethernet to Starlink Go (future) — we can add dedicated logic later
        # Unknown — UI shows "-"
        return None

    def _looks_like_wireless(self, iface: str) -> bool:
        try:
            out = subprocess.check_output(
                ["iw", "dev", iface, "info"],
                stderr=subprocess.DEVNULL,
                timeout=1
            )
            return True
        except Exception:
            return False

    def _get_wifi_signal_quality(self, iface: str) -> str | None:
        """Try to get RSSI from a WiFi client interface and turn it into percent + state."""
        try:
            # Works well for station/client mode
            out = subprocess.check_output(
                ["iw", "dev", iface, "station", "dump"],
                stderr=subprocess.DEVNULL,
                timeout=2
            ).decode(errors="ignore")

            for line in out.splitlines():
                line = line.strip()
                if line.startswith("signal:"):
                    parts = line.split()
                    if len(parts) >= 2:
                        try:
                            rssi = int(parts[1])
                            return self._rssi_to_quality_string(rssi)
                        except ValueError:
                            continue
        except Exception:
            pass
        return None

    def _rssi_to_quality_string(self, rssi: int) -> str:
        """Map RSSI (dBm, negative) to a nice '92%' string (no parenthetical description, to keep network tile compact)."""
        if rssi >= -50:
            pct = 100
        elif rssi >= -60:
            pct = 90
        elif rssi >= -70:
            pct = 75
        elif rssi >= -80:
            pct = 50
        elif rssi >= -85:
            pct = 30
        else:
            pct = 10
        return f"{pct}%"

    def _rssi_dbm_to_percent(self, rssi: int) -> int:
        """Map RSSI (dBm, negative) to 0-100 percent. Used by iw-based WiFi scans."""
        if rssi >= -50:
            return 100
        elif rssi >= -60:
            return 90
        elif rssi >= -70:
            return 75
        elif rssi >= -80:
            return 50
        elif rssi >= -85:
            return 30
        else:
            return 10

    def _get_usb_cellular_signal_quality(self) -> str | None:
        """Best-effort attempt to read cellular signal strength from a USB-tethered phone.

        Many Android phones in USB tether mode expose a serial port we can send AT
        commands to. We try common ports and common Telstra/Android commands.
        """
        import serial
        import glob

        # Common ports seen with Android USB tethering / modem mode
        candidates = glob.glob("/dev/ttyUSB*") + glob.glob("/dev/ttyACM*")

        # Sort so we try lower numbers first (often the diagnostic port)
        candidates.sort()

        for port in candidates:
            try:
                with serial.Serial(port, 115200, timeout=1) as ser:
                    # Wake up the modem
                    ser.write(b"AT\r")
                    resp = ser.read(100).decode(errors="ignore")
                    if "OK" not in resp:
                        continue

                    # Try AT+CSQ first (most universal)
                    ser.write(b"AT+CSQ\r")
                    resp = ser.read(200).decode(errors="ignore")
                    if "+CSQ:" in resp:
                        # Format is usually +CSQ: <rssi>,<ber>
                        try:
                            rssi = int(resp.split("+CSQ:")[1].split(",")[0].strip())
                            # Convert CSQ (0-31) to dBm approx: dBm = -113 + (rssi * 2)
                            dbm = -113 + (rssi * 2)
                            return self._rssi_to_quality_string(dbm)
                        except Exception:
                            pass

                    # Try Telstra / newer Android: AT+CPSI?
                    ser.write(b"AT+CPSI?\r")
                    resp = ser.read(300).decode(errors="ignore")
                    if "+CPSI:" in resp:
                        # Example: +CPSI: 0,1,"46001",...
                        # We can look for signal related fields in some responses
                        # For many modems this gives more detailed info; fall back to CSQ if possible
                        pass

            except (serial.SerialException, OSError):
                continue

        return None

    def _get_starlink_signal_quality(self) -> str | None:
        """Try to fetch signal quality from a local Starlink dish/router."""
        candidates = [
            "http://192.168.100.1/api/status",
            "http://192.168.100.1/api/v1/status",
            "http://192.168.100.1/api/device/status",
        ]
        for url in candidates:
            try:
                import urllib.request
                with urllib.request.urlopen(url, timeout=2) as resp:
                    data = json.loads(resp.read().decode())
                # Starlink v3/v4 style
                if "signalQuality" in data:
                    sq = data["signalQuality"]
                    if isinstance(sq, (int, float)):
                        pct = int(sq * 100)
                        return f"{pct}%"
                # Older / other shapes
                if "snr" in data or "signal" in data:
                    # rough mapping if we have dB values
                    pass
            except Exception:
                continue
        return None

    # ====================== WIFI CLIENT MANAGEMENT (for /diag WiFi chooser tile) ======================
    # Uses nmcli (NetworkManager) as the primary, robust tool for scanning and connecting.
    # Falls back with clear errors if nmcli is unavailable (common on systems using
    # only wpa_supplicant + dhcpcd). All subprocess calls use list form (never shell=True
    # when a password may be involved).

    def get_current_wifi(self) -> dict | None:
        """Return basic info about the current WiFi client connection, if any.
        Used both for the passive Network Details and to seed the WiFi tile.
        """
        try:
            # Prefer nmcli for the active connection name on wlan interfaces
            out = subprocess.check_output(
                ["nmcli", "-t", "-f", "NAME,TYPE,DEVICE", "connection", "show", "--active"],
                stderr=subprocess.DEVNULL,
                timeout=3
            ).decode(errors="ignore")

            for line in out.splitlines():
                if not line.strip():
                    continue
                parts = line.split(":")
                if len(parts) >= 3 and "wifi" in parts[1].lower():
                    name = parts[0] or "Unknown"
                    dev = parts[2] or "wlan0"
                    # Try to get IP for that dev
                    ip = None
                    try:
                        ip_out = subprocess.check_output(
                            f"ip -4 addr show {dev} 2>/dev/null | grep -oP '(?<=inet\\s)[0-9.]+'",
                            shell=True, timeout=2
                        ).decode().strip()
                        if ip_out:
                            ip = ip_out
                    except Exception:
                        pass
                    return {"ssid": name, "iface": dev, "ip": ip, "connected": True}
        except FileNotFoundError:
            # nmcli not present — fall back to iw + ip for best-effort info
            pass
        except Exception:
            pass

        # Fallback: look for a wlan* interface that is associated
        for iface in ("wlan0", "wlan1"):
            try:
                link = subprocess.check_output(
                    ["iw", "dev", iface, "link"],
                    stderr=subprocess.DEVNULL,
                    timeout=2
                ).decode(errors="ignore")
                if "SSID" in link:
                    ssid = None
                    for ln in link.splitlines():
                        if "SSID:" in ln:
                            ssid = ln.split("SSID:", 1)[1].strip()
                            break
                    ip = None
                    try:
                        ip_out = subprocess.check_output(
                            f"ip -4 addr show {iface} 2>/dev/null | grep -oP '(?<=inet\\s)[0-9.]+'",
                            shell=True, timeout=2
                        ).decode().strip()
                        if ip_out:
                            ip = ip_out
                    except Exception:
                        pass
                    return {"ssid": ssid or "Associated", "iface": iface, "ip": ip, "connected": True}
            except Exception:
                continue

        return {"ssid": None, "connected": False}

    def _scan_with_iw(self, iface: str) -> list[dict]:
        """Best-effort WiFi scan using `iw` as a supplement to nmcli.

        This often discovers iPhone/Android personal hotspots (and other APs on
        alternate channels/bands) that `nmcli device wifi list` misses, especially
        right after the system is associated to house WiFi (radio may stay on the
        house channel for a while; NM's view can lag).
        """
        results: list[dict] = []
        try:
            out = subprocess.check_output(
                ["iw", "dev", iface, "scan"],
                stderr=subprocess.DEVNULL,
                timeout=10
            ).decode(errors="ignore")
        except Exception:
            return []

        current = None
        for raw in out.splitlines():
            line = raw.strip()
            if not line:
                continue
            if line.startswith("BSS "):
                if current and current.get("ssid"):
                    results.append(current)
                current = {
                    "ssid": None,
                    "signal": None,
                    "security": "open",
                    "in_use": False,
                }
                continue
            if current is None:
                continue
            if line.startswith("SSID:"):
                ssid = line.split("SSID:", 1)[1].strip()
                # Hidden networks often show empty or nulled SSID; skip (UX + no way to pick name)
                if ssid and not ssid.startswith("\x00"):
                    current["ssid"] = ssid
            elif line.startswith("signal:"):
                try:
                    val = line.split("signal:", 1)[1].strip().split()[0]  # "-57.00"
                    dbm = int(float(val))
                    current["signal"] = self._rssi_dbm_to_percent(dbm)
                except Exception:
                    pass
            elif line.startswith("capability:"):
                if "Privacy" in line:
                    current["security"] = "secured"
            elif "RSN:" in line:
                if current.get("security") != "open":
                    current["security"] = "WPA2"
            elif "WPA:" in line and current.get("security") not in ("open", "WPA2"):
                current["security"] = "WPA"
        if current and current.get("ssid"):
            results.append(current)
        return results

    def wifi_scan(self) -> list[dict]:
        """Scan for available WiFi networks.
        Returns list of dicts: {ssid, signal (int 0-100 or None), security (str), in_use (bool)}
        Primary: nmcli (terse). Falls back/supplements with `iw` scan to reliably pick up
        iPhone and other mobile hotspots that are often missed by NM's cached view.
        """
        networks = []
        try:
            # Explicit rescan + short settle time dramatically improves visibility of
            # phone hotspots (iPhone personal hotspot, work iPhone, Android, etc.).
            # These often live on different channels than the house AP you're currently
            # associated to; NM can lag until it does a background scan.
            try:
                subprocess.run(
                    ["nmcli", "device", "wifi", "rescan"],
                    capture_output=True, timeout=5, stderr=subprocess.DEVNULL
                )
                time.sleep(1.0)
            except Exception:
                pass

            out = subprocess.check_output(
                ["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY,IN-USE", "device", "wifi", "list", "--rescan", "auto"],
                stderr=subprocess.DEVNULL,
                timeout=15
            ).decode(errors="ignore")
        except FileNotFoundError:
            logger.warning("nmcli not found — WiFi scan requires NetworkManager. Install with: sudo apt install network-manager")
            return []
        except subprocess.TimeoutExpired:
            logger.warning("WiFi scan timed out")
            return []
        except Exception as e:
            logger.debug(f"nmcli wifi scan failed: {e}")
            return []

        for line in out.strip().split("\n"):
            if not line.strip():
                continue
            # SSID can contain ":", so take the last 3 fields as SIGNAL:SECURITY:IN-USE
            parts = line.rsplit(":", 3)
            if len(parts) != 4:
                continue
            ssid, signal_str, security, in_use_str = parts
            ssid = ssid.strip()
            if not ssid:
                # nmcli sometimes emits an empty SSID line for hidden networks; skip for UX
                continue

            try:
                signal = int(signal_str) if signal_str.strip().isdigit() else None
            except Exception:
                signal = None

            security = security.strip() or "open"
            in_use = in_use_str.strip().lower() in ("yes", "*")

            # Normalise a few common security labels for the frontend
            sec_lower = security.lower()
            if "--" in sec_lower or sec_lower in ("", "open"):
                security = "open"
            elif "wpa3" in sec_lower:
                security = "WPA3"
            elif "wpa2" in sec_lower or "wpa" in sec_lower:
                security = "WPA2"
            elif "wep" in sec_lower:
                security = "WEP"
            else:
                security = security or "secured"

            networks.append({
                "ssid": ssid,
                "signal": signal,
                "security": security,
                "in_use": in_use
            })

        # Supplement with networks discovered via direct `iw` scans on common WiFi ifaces.
        # This is the key fix for iPhone (and "kiphone") personal hotspots not appearing
        # in the selection list even though house WiFi does.
        for iface in ("wlan0", "wlan1"):
            for n in self._scan_with_iw(iface):
                if n.get("ssid"):
                    networks.append(n)

        # De-dupe by SSID while preferring the in-use / strongest entry
        seen = {}
        for n in networks:
            key = n["ssid"]
            if key not in seen or (n["in_use"] and not seen[key]["in_use"]) or (n.get("signal") or 0) > (seen[key].get("signal") or 0):
                seen[key] = n
        result = list(seen.values())
        result.sort(key=lambda x: (not x["in_use"], -(x["signal"] or 0)))
        return result

    def wifi_connect(self, ssid: str, password: str | None = None) -> dict:
        """Attempt to connect to the given SSID.
        Returns {"success": bool, "message": str, "ssid": str}
        Never logs the password. Uses list-form subprocess.
        """
        if not ssid:
            return {"success": False, "message": "No SSID provided", "ssid": None}

        cmd = ["nmcli", "device", "wifi", "connect", ssid]
        if password:
            cmd += ["password", password]

        try:
            # Capture both stdout and stderr for useful error messages
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=25
            )
            out = (proc.stdout or "") + (proc.stderr or "")
            out = out.strip()

            if proc.returncode == 0:
                logger.info(f"📶 WiFi connect succeeded for SSID: {ssid}")
                return {"success": True, "message": f"Connected to {ssid}", "ssid": ssid}

            # Common friendly messages from nmcli
            msg = out or "Connection failed"
            if "Secrets were required" in msg or "password" in msg.lower():
                msg = "Password required or incorrect"
            elif "No network with SSID" in msg or "not found" in msg.lower():
                msg = f"No network found with SSID '{ssid}'"
            elif "Device" in msg and "not ready" in msg.lower():
                msg = "WiFi device is not ready"

            logger.warning(f"📶 WiFi connect failed for SSID '{ssid}': {msg[:200]}")
            return {"success": False, "message": msg, "ssid": ssid}

        except FileNotFoundError:
            logger.warning("nmcli not found — cannot perform WiFi connect (NetworkManager required)")
            return {"success": False, "message": "nmcli (NetworkManager) is not installed on this system", "ssid": ssid}
        except subprocess.TimeoutExpired:
            return {"success": False, "message": "Connection attempt timed out", "ssid": ssid}
        except Exception as e:
            logger.error(f"Unexpected error during wifi_connect for {ssid}: {e}")
            return {"success": False, "message": str(e), "ssid": ssid}

    # ====================== NEW: COMPACT NETWORK STATUS FOR DASHBOARD TILE ======================

    _FRIENDLY_IFACE: dict = {
        "wlan0": "WiFi",
        "wlan1": "WiFi 2",
        "eth0": "Ethernet",
        "eth1": "Ethernet 2",
        "usb0": "USB",
        "rndis0": "USB",
        "enp0s3": "Ethernet",
        "ppp0": "Cellular",
        "wwan0": "Cellular",
    }

    def _get_friendly_interface_name(self, iface: str) -> str:
        if not iface:
            return "Network"
        for key, friendly in self._FRIENDLY_IFACE.items():
            if iface == key or (key[:-1] and iface.startswith(key[:-1])):
                return friendly
        if iface.startswith("wlan"):
            return "WiFi"
        if iface.startswith("eth") or iface.startswith("en"):
            return "Ethernet"
        if iface.startswith("usb") or iface.startswith("rndis"):
            return "USB"
        if iface.startswith("ppp") or iface.startswith("wwan"):
            return "Cellular"
        return iface.upper()[:12]

    def _get_link_speed(self, iface: str) -> int | None:
        """Best-effort link speed in Mbps (sysfs for wired, iw for wireless)."""
        if not iface:
            return None
        try:
            speed_path = f"/sys/class/net/{iface}/speed"
            if os.path.exists(speed_path):
                with open(speed_path) as f:
                    val = int(f.read().strip())
                    if val > 0:
                        return val
        except Exception:
            pass
        try:
            out = subprocess.check_output(
                f'iw dev {iface} link 2>/dev/null | grep -i "tx bitrate" || true',
                shell=True, timeout=2
            ).decode().strip().lower()
            if "bitrate" in out:
                for part in out.split():
                    if part.replace(".", "", 1).isdigit():
                        return int(round(float(part)))
        except Exception:
            pass
        return None

    def _sample_throughput(self):
        """Compute KB/s deltas. Targets the last known upstream iface when possible."""
        now = time.time()
        try:
            curr = psutil.net_io_counters(pernic=True)
        except Exception:
            return 0.0, 0.0

        prev = getattr(self, "_prev_net_io", None)
        prev_ts = getattr(self, "_prev_net_io_ts", 0.0)
        upstream = getattr(self, "_last_upstream_iface", None)
        dt = now - prev_ts if prev_ts else 0.0

        def _kbps(delta: int, dt: float) -> float:
            return max(0.0, round((delta / 1024.0) / dt, 1)) if dt > 0.05 else 0.0

        rx_k = tx_k = 0.0
        if upstream and prev and upstream in curr and upstream in prev:
            rx_k = _kbps(curr[upstream].bytes_recv - prev[upstream].bytes_recv, dt)
            tx_k = _kbps(curr[upstream].bytes_sent - prev[upstream].bytes_sent, dt)
        elif prev:
            try:
                tot = psutil.net_io_counters()
                tot_prev = getattr(self, "_prev_tot_io", None)
                if tot_prev:
                    rx_k = _kbps(tot.bytes_recv - tot_prev.bytes_recv, dt)
                    tx_k = _kbps(tot.bytes_sent - tot_prev.bytes_sent, dt)
            except Exception:
                pass

        self._prev_net_io = curr
        self._prev_net_io_ts = now
        try:
            self._prev_tot_io = psutil.net_io_counters()
        except Exception:
            pass
        return rx_k, tx_k

    def get_network_status(self):
        """Compact payload for the main UI Network tile (internet half + basics)."""
        status = {
            "internet": {
                "connected": False,
                "upstream_iface": None,
                "friendly_name": "No Internet",
                "gateway": None,
                "link_speed_mbps": None,
                "rx_kbps": 0.0,
                "tx_kbps": 0.0,
            },
            "last_updated": datetime.now().isoformat(),
        }
        via_iface = None
        gw_ip = None
        try:
            output = subprocess.check_output(
                "ip route get 8.8.8.8", shell=True, timeout=2.5
            ).decode().strip()
            for line in output.splitlines():
                if "via" in line:
                    parts = line.split()
                    for i, p in enumerate(parts):
                        if p == "via" and i + 1 < len(parts):
                            gw_ip = parts[i + 1]
                        elif p == "dev" and i + 1 < len(parts):
                            via_iface = parts[i + 1]
                    if gw_ip and via_iface:
                        break
        except Exception as e:
            logger.debug(f"get_network_status gateway probe: {e}")

        if via_iface:
            status["internet"]["connected"] = True
            status["internet"]["upstream_iface"] = via_iface
            status["internet"]["friendly_name"] = self._get_friendly_interface_name(via_iface)
            status["internet"]["gateway"] = gw_ip
            self._last_upstream_iface = via_iface
            spd = self._get_link_speed(via_iface)
            if spd:
                status["internet"]["link_speed_mbps"] = spd

            # Signal quality (new)
            sig = self._get_current_signal_quality(via_iface)
            if sig:
                status["internet"]["signal_quality"] = sig

        rx, tx = self._sample_throughput()
        status["internet"]["rx_kbps"] = rx
        status["internet"]["tx_kbps"] = tx
        return status
