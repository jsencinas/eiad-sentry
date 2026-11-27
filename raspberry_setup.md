# Connecting a Raspberry Pi to enterprise WiFi (WPA/WPA2 Enterprise) over SSH

Documentation of the whole process: what we did, why, and how to repeat it or fix it if something breaks.

---

## 1. The problem

Raspberry Pi Imager can only configure **personal** WiFi networks (SSID + shared password). It has no fields for **enterprise** networks (WPA/WPA2 Enterprise), which need a username, EAP method, anonymous identity, certificates, etc.

The Tec network (`Tec`) uses:

| Field | Value |
|---|---|
| Security | WPA & WPA2 Enterprise |
| Authentication | PEAP |
| Anonymous identity | `Tec` |
| Domain | `wireless.itesm.mx` (not used without a CA certificate) |
| CA certificate | None ("No CA certificate is required") |
| PEAP version | Automatic |
| Inner authentication | MSCHAPv2 |

## 2. The solution in short

1. Flash the Pi with Imager using the **phone hotspot** as WiFi.
2. Log in to the Pi over **SSH** from the laptop (both on the hotspot).
3. Install **Tailscale** on the Pi and on the laptop, so you can get back in no matter which network the Pi is on.
4. Create the **enterprise WiFi profile** with `nmcli` (script).
5. **Switch the Pi's network** (with a safety timer the first time).
6. Reconnect using the **Tailscale IP**.

---

## 3. Key concepts

### 3.1 SSH: which computer the terminal belongs to

When you run `ssh jorge@IP`, your terminal starts **controlling the Pi**. Whatever you type runs there. The prompt shows it:

- `jorge@envy` → laptop
- `jorge@raspberrio` → Pi

`exit` takes you back to the laptop.

### 3.2 The Pi has a single WiFi antenna

When it joins the enterprise network, it **leaves the hotspot**. The SSH session that went through the hotspot dies, and the Pi's IP changes (the enterprise network assigns a different one).

### 3.3 Tailscale

Creates a private virtual network between **your own devices**:

- Each device gets a fixed `100.x.y.z` IP that **does not change** even when the network changes.
- It runs on a virtual interface (`tailscale0`), separate from `wlan0`.
- It uses the `100.64.0.0/10` range, which almost never clashes with real networks (`10.x`, `172.16.x`, `192.168.x`).
- It needs: the same login on both devices and internet access on both.

It does not connect the Pi to WiFi; it only gives you a fixed address to **get back in**.

### 3.4 NetworkManager and profiles

NetworkManager stores connection **profiles**. `nmcli connection add` only **saves** a profile; it does not activate it. It is activated with `nmcli connection up` or on reboot (if it has `autoconnect`).

If several profiles are available, the one with the highest `autoconnect-priority` wins.

### 3.5 Netplan

The hotspot profile is called `netplan-wlan0-Galaxy S26 Ultra 7FE1`. The `netplan-wlan0-` prefix means Imager created it through cloud-init/netplan. You must use the **full** name with `nmcli`.

---

## 4. Step by step

### Step 0: Flash the Pi

In Raspberry Pi Imager (with elevated permissions if it asks; the app's own prompt is better than running everything with `sudo`):

- Hostname (here: `raspberrio`), user (`jorge`) and password.
- WiFi: the **phone hotspot** (SSID, password and **WiFi country**; without a country, the radio may stay disabled).
- **Enable SSH.**
- Double-check the target device before writing (so you don't wipe the wrong disk).

> On Arch there is also `sudo pacman -S rpi-imager`. The AppImage needs `chmod +x` and sometimes `fuse2`.

### Step 1: Find the Pi's IP on the hotspot

- Check the list of connected devices on the phone, or
- Scan: `nmap -sn 10.53.109.0/24` (use your laptop's subnet; see it with `ip -4 addr`).
- Confirm with `ping -c 3 IP` and `ip neigh`. A Pi's MAC address starts with `2c:cf:67` (or a similar Raspberry Pi Trading prefix).

**Errors we ran into:**

| Attempt | Problem |
|---|---|
| `ssh jorge@hostname.localhost` | `.localhost` points to your own laptop. It must be `.local` (or use the IP). |
| `ssh jorge@raspberrio.10.53.109.82` | Mixes hostname and IP. Use one or the other. |
| `No route to host` | It was temporary (ARP not resolved yet, the Pi was still booting). Retrying worked. |

### Step 2: Connect over SSH

```bash
ssh jorge@10.53.109.82
```

### Step 3: Install Tailscale on the Pi (while on the hotspot)

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```

Open the link it prints and log in. Then:

```bash
tailscale ip -4      # the Pi's Tailscale IP
```

On the Pi, the service must be enabled to start on boot:

```bash
systemctl is-enabled tailscaled     # must print "enabled"
```

### Step 4: Install Tailscale on the laptop (Arch)

```bash
sudo pacman -S tailscale
sudo systemctl start tailscaled     # or "enable --now" to always start it
sudo tailscale up                   # same account as the Pi
tailscale status                    # must list the Pi
```

- `enable --now` = start now **and** on every boot.
- `start` = this session only.
- `tailscaled` running does not mean you are connected: that is controlled by `tailscale up` / `tailscale down`.

Stop and disable:

```bash
sudo tailscale down
sudo systemctl disable --now tailscaled
```

### Step 5: Create the enterprise WiFi profile (script)

Save it on the Pi as `wifi.sh`:

```bash
#!/bin/bash
set -e

CON_NAME="Tec-wifi"
SSID="Tec"
USER_ID='your_username'
PASS='your_password'

# Delete the profile if it already exists (avoids duplicates)
sudo nmcli connection delete "$CON_NAME" 2>/dev/null || true

sudo nmcli connection add type wifi con-name "$CON_NAME" ifname wlan0 ssid "$SSID" \
  wifi-sec.key-mgmt wpa-eap \
  802-1x.eap peap \
  802-1x.anonymous-identity "Tec" \
  802-1x.phase2-auth mschapv2 \
  802-1x.identity "$USER_ID" \
  802-1x.password "$PASS" \
  connection.autoconnect yes \
  connection.autoconnect-priority 10

echo "Profile added. It is not active yet, so your SSH session is still alive."
```

Run it:

```bash
chmod 700 wifi.sh
./wifi.sh
```

Notes:

- `USER_ID` is the **WiFi network** user (student ID or institutional email), **not** `jorge`.
- Single quotes around `PASS` keep `$`, `"`, `` ` `` and `\` from being interpreted. A single quote inside is written as `'\''`.
- `ca-cert` and `domain-suffix-match` are not used, because the network does not require a certificate.
- The profile name is the one given in `con-name` (`Tec-wifi` on this Pi). Always use the name shown by `nmcli connection show`.
- This script **does not cut SSH**, because it only saves the profile.

Verify:

```bash
nmcli connection show Tec-wifi | grep -E "802-1x.(eap|identity|anonymous)"
```

### Step 6: Switch networks with a safety timer

The first time, it is worth testing with an automatic return to the hotspot:

```bash
sudo nohup sh -c 'nmcli connection up Tec-wifi; sleep 120; nmcli connection up "netplan-wlan0-Galaxy S26 Ultra 7FE1"' >/dev/null 2>&1 &
```

Details:

- The double quotes go **around the full profile name** (it has spaces). An early mistake was writing `Galaxy "S26 Ultra 7FE1"`, which splits the name into two arguments.
- The SSH session freezes. That is expected: the Pi left the hotspot.
- To cancel the return before the 120 s are up (if you still have access): `sudo pkill -f "sleep 120"`.
- If you turn off the hotspot, the return command fails and the Pi **stays on `Tec`**, which is what we want.

### Step 7: Close the frozen SSH session

The TCP connection died but the laptop doesn't know it. `Ctrl+D` does not work. Use the SSH escape sequence:

1. `Enter`
2. `~`
3. `.`

Or, in tmux: `Ctrl+B` then `x` to close the pane.

### Step 8: Verify from the Pi (or over Tailscale)

```bash
hostname -I
nmcli connection show --active
```

Result we got:

```
10.43.50.144 100.74.216.50 fd7a:115c:a1e0::1c49:d833

Tec-wifi    ...  wifi      wlan0
tailscale0  ...  tun       tailscale0
lo          ...  loopback  lo
```

- `10.43.50.144` → IP on the enterprise network (on `wlan0`).
- `100.74.216.50` → the Pi's Tailscale IP (fixed).
- `fd7a:...` → Tailscale IPv6 (ignore).

### Step 9: Log in from the laptop over Tailscale

```bash
ssh jorge@100.74.216.50
```

The laptop can be on **any network** with internet, as long as Tailscale is up.

### Step 10: Make it permanent

```bash
sudo nmcli connection modify Tec-wifi connection.autoconnect yes connection.autoconnect-priority 10
systemctl is-enabled tailscaled
```

After every reboot, the Pi will connect to `Tec` and be reachable at `100.74.216.50`.

---

## 5. How the fallback works (if `Tec` fails)

- **Authentication fails or the network is out of range:** NetworkManager marks the profile as failed and falls back to the next available one (the hotspot). Log in again with `ssh jorge@10.53.109.82` (with the hotspot on).
- **It connects but doesn't work** (captive portal, unregistered MAC, client isolation): NM considers it successful and **does not fall back**. Tailscale is the safety net here, as long as the network has internet access.
- **Last resort:** monitor and keyboard on the Pi, or edit the configuration on the SD card from the laptop.

To test without the risk of a reboot sending you to `Tec` by surprise, create the profile with `autoconnect no` and only enable it once it is confirmed.

---

## 6. Quick diagnosis

| Symptom | What to check |
|---|---|
| `Connection refused` | Something answers, but SSH is not enabled, or the hostname/IP is wrong (`.localhost`). |
| `No route to host` | No ARP reply: the Pi is still booting, wrong IP, different subnet or client isolation. |
| The Pi shows up on the phone but doesn't respond | "Isolate clients" on the hotspot; try the 2.4 GHz band. |
| `.local` doesn't resolve on Arch | Install `avahi` and `nss-mdns`, enable `avahi-daemon` and edit `hosts:` in `/etc/nsswitch.conf`. Or use the IP. |
| You don't know the Pi's new IP | Use Tailscale (`tailscale status` on the laptop) or `hostname -I` on the Pi. |
| Authentication on `Tec` fails | Check the logs: `journalctl -u NetworkManager --since "15 minutes ago" \| grep -iE "tec\|auth\|fail\|activated"` |
| Tailscale doesn't connect on the enterprise network | The network may block internet access or Tailscale traffic. |
| Profile not found | Exact name with `nmcli connection show`. |

Useful commands:

```bash
nmcli connection show                 # all profiles
nmcli connection show --active        # active profiles
nmcli device status                   # interface status
ip -4 addr show wlan0                 # current IP
tailscale status                      # Tailscale devices
```

---

## 7. Security

- `nmcli` stores the password in **plain text** in `/etc/NetworkManager/system-connections/` (only root can read it).
- If you leave the password in `wifi.sh`, protect it with `chmod 700` and **delete it or clear the password** when you are done. Don't push it to GitHub.
- Passing the password as an argument (`./script.sh 'pass'`) leaves it in the shell history.
- Don't add your user to the `disk` group as a permanent fix: it gives near-root access to the disks.
- Without a CA certificate, the Pi does not verify the network server's identity. That is what the Tec network instructs, but it means less protection against rogue networks.

---

## 8. Summary of this installation

| Item | Value |
|---|---|
| Laptop | `envy` (Arch Linux) |
| Pi | hostname `raspberrio`, user `jorge` |
| Hotspot profile | `netplan-wlan0-Galaxy S26 Ultra 7FE1` |
| Enterprise profile | `Tec-wifi` |
| IP on hotspot | `10.53.109.82` |
| IP on `Tec` | `10.43.50.144` (may change) |
| Tailscale IP | `100.74.216.50` (fixed) |
| Log in to the Pi | `ssh jorge@100.74.216.50` |

---

## 9. Short recipe (to repeat on another Pi)

1. Flash with Imager (hotspot WiFi, SSH enabled, country set).
2. `ssh user@hotspot_IP`
3. Install Tailscale on the Pi and on the laptop; check with `tailscale status`.
4. Run the `nmcli` script with the SSID, user and password.
5. Activate it with the safety timer (or `sudo reboot`).
6. Reconnect with `ssh user@tailscale_IP`.
7. Leave `autoconnect yes` and delete the password from the script.
