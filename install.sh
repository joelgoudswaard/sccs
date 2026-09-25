#!/usr/bin/env bash
# SCCS installer & setup utility
#
# Bootstrap (any directory — installs to ~/sccs):
#   curl -fsSL https://raw.githubusercontent.com/joelgoudswaard/sccs/main/install.sh \
#     -o /tmp/sccs-install.sh && sudo bash /tmp/sccs-install.sh
#
# After install (or from a checkout of install.sh alone):
#   sudo ./install.sh              # interactive menu
#   sudo ./install.sh --install    # Install SCCS
#   sudo ./install.sh --update     # git pull + deps + Pi-hole + touchscreen apt
#   sudo ./install.sh --esp        # ESP32 flash only
#   sudo ./install.sh --victron    # Victron only
#   sudo ./install.sh --sensors    # 1-Wire only
#   sudo ./install.sh --lan        # LAN / DHCP / NAT only
#   sudo ./install.sh --usb-tether # Guided phone USB internet
#   sudo ./install.sh --screens    # Scan LAN, add touchscreen, SSH + desktop login + Chromium UI
#   sudo ./install.sh --service    # rewrite/restart systemd unit
#   sudo ./install.sh --voice      # HomeKit / Siri, Google Home / Gemini, or both
#   sudo ./install.sh --help
#
# Install location is always ~/sccs for the invoking user (override with SCCS_HOME).
# The script clones the public repo there if needed; cwd does not matter.
# UART/GPIO mapping is fixed by the PCB + stock config.
#
# Do not pipe this script into bash (curl|bash). Interactive menus need a real
# script file so stdin stays on the terminal. Use the bootstrap line above.
set -euo pipefail

# When run as a file (bash /tmp/sccs-install.sh), reattach stdin to the
# controlling TTY if it was lost (common under some sudo / non-tty setups).
# Safe only for file-based invocation — never use with curl|bash.
if [[ ! -t 0 ]] && ( : </dev/tty ) 2>/dev/null; then
    exec </dev/tty
fi

# ---------------------------------------------------------------------------
# Defaults (PCB-fixed — not prompted)
# ---------------------------------------------------------------------------
# Public repo — HTTPS clone/pull, no credentials.
REPO_URL="${REPO_URL:-https://github.com/joelgoudswaard/sccs.git}"
SERVICE_NAME="${SERVICE_NAME:-sccs}"
LAN_ADDR="${LAN_ADDR:-10.10.10.1}"
# Control-Pi UI URL used as Chromium homepage / autostart on touchscreens (nginx :80).
SCCS_UI_URL="${SCCS_UI_URL:-http://${LAN_ADDR}/}"
# Board default is Hardware CDC/JTAG (usb_mode=1). USBMode=default is TinyUSB
# OTG and can hang setup() on these modules, which have no USB.
ESP_FQBN="${ESP_FQBN:-esp32:esp32:esp32s3}"
W1_GPIO="${W1_GPIO:-3}"
# Header pin 37. R5 pulls GPIO26 up to 3.3V when the Pi is on the SCCS Core
# (schematic note: "SCCS core hardware / Presence detection"). The sheet value
# is 4.7k; either that or 10k beats the Pi's ~50k internal pulldown, so the
# pin reads high only with the Core fitted.
SCCS_DETECT_GPIO="${SCCS_DETECT_GPIO:-26}"

# ---------------------------------------------------------------------------
# Colours (subtle; disabled when not a TTY)
# ---------------------------------------------------------------------------
if [[ -t 1 ]]; then
    C_RESET=$'\033[0m'
    C_BOLD=$'\033[1m'
    C_DIM=$'\033[2m'
    C_GREEN=$'\033[32m'
    C_YELLOW=$'\033[33m'
    C_RED=$'\033[31m'
    C_CYAN=$'\033[36m'
else
    C_RESET= C_BOLD= C_DIM= C_GREEN= C_YELLOW= C_RED=
    C_CYAN=
fi

STEP=0
TOTAL_STEPS=0
NEED_REBOOT=0
GROUPS_CHANGED=0
UART_REBOOT_DECLINED=0
UARTS_LIVE=0
SCCS_RESUME_PHASE=""
SKIPPED_NOTES=()
REQUIRED_GROUPS=(www-data tty dialout gpio netdev)
# Written before a restart that the ESP flash is waiting on. The copy of this
# script is what continues the install — the clone under ~/sccs may be older
# than the installer the user actually ran.
INSTALL_RESUME_DIR="/var/lib/sccs"
INSTALL_RESUME_FILE="/var/lib/sccs/install-resume"
INSTALL_RESUME_SCRIPT="/var/lib/sccs/install-resume.sh"
INSTALL_RESUME_PROFILE="/etc/profile.d/sccs-install-resume.sh"
INSTALL_RESUME_SUDOERS="/etc/sudoers.d/sccs-install-resume"
INSTALL_RESUME_LOCK="/var/lib/sccs/install-resume.lock"
# Set when Pi-hole DHCP is turned on. The next touchscreen scan asks for a
# panel restart first — already-powered screens have no lease from this server.
DHCP_PANEL_RESTART_FLAG="/var/lib/sccs/dhcp-panels-need-restart"
RUN_MODE="menu"   # menu | install | sensors | esp | victron | lan | screens | service

# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------
# Single source of truth: repo-root VERSION (same file the app reads).
# No second constant to keep in sync when you bump the version.
sccs_version() {
    local f v
    for f in \
        ${SCCS_HOME:+"$SCCS_HOME/VERSION"} \
        ${SCRIPT_DIR:+"$SCRIPT_DIR/VERSION"}
    do
        [[ -r "$f" ]] || continue
        v="$(tr -d '[:space:]' <"$f" 2>/dev/null || true)"
        [[ -n "$v" ]] && { printf '%s\n' "$v"; return 0; }
    done
    return 1
}

logo() {
    local ver
    echo
    echo "${C_CYAN}${C_BOLD}"
    cat <<'EOF'
   ███████╗  ██████╗  ██████╗ ███████╗
  ██╔════╝ ██╔════╝ ██╔════╝ ██╔════╝
  ███████╗ ██║      ██║      ███████╗
  ╚════██║ ██║      ██║      ╚════██║
  ███████║ ╚██████╗ ╚██████╗ ███████║
   ╚══════╝  ╚═════╝  ╚═════╝ ╚══════╝
EOF
    # Show "· vX.Y…" only when VERSION is on disk (checkout / after clone).
    # A lone downloaded install.sh has no VERSION yet — name only, never "vunknown".
    if ver="$(sccs_version)"; then
        echo "${C_RESET}${C_DIM}  Singularity Camper Control System · v${ver}${C_RESET}"
    else
        echo "${C_RESET}${C_DIM}  Singularity Camper Control System${C_RESET}"
    fi
    hr
    echo
}

hr() {
    echo "${C_DIM}  ────────────────────────────────────────────────────────${C_RESET}"
}

section_title() {
    echo
    echo "${C_BOLD}${C_CYAN}  ▸ $*${C_RESET}"
    hr
}

step_begin() {
    STEP=$((STEP + 1))
    echo
    if [[ "$TOTAL_STEPS" -gt 0 ]]; then
        echo "${C_BOLD}${C_CYAN}  [$STEP/$TOTAL_STEPS]${C_RESET} ${C_BOLD}$*${C_RESET}"
    else
        echo "${C_BOLD}${C_CYAN}  ›${C_RESET} ${C_BOLD}$*${C_RESET}"
    fi
    echo "${C_DIM}  ────────────────────────────────────────────────────────${C_RESET}"
}

info()      { echo "  ${C_DIM}•${C_RESET} $*"; }
ok()        { echo "  ${C_GREEN}✓${C_RESET} $*"; }
warn()      { echo "  ${C_YELLOW}!${C_RESET} $*"; }
fail()      { echo "  ${C_RED}✗${C_RESET} $*" >&2; }
die()       { fail "$*"; exit 1; }
skip_note() { SKIPPED_NOTES+=("$*"); warn "$*"; }

# Interactive line input. Prefer the controlling TTY so menus still work when
# stdin is not a terminal (sudo quirks, redirected stdin, etc.).
# Sets REPLY_LINE. Returns 0 on success, 1 on EOF / no terminal.
read_input() {
    local prompt="$1"
    REPLY_LINE=""
    if ( : </dev/tty ) 2>/dev/null; then
        read -r -p "$prompt" REPLY_LINE </dev/tty || return 1
    elif [[ -t 0 ]]; then
        read -r -p "$prompt" REPLY_LINE || return 1
    else
        return 1
    fi
    return 0
}

ask_yn() {
    local prompt="$1" def="${2:-n}" hint ans
    if [[ "$def" == "y" ]]; then hint="Y/n"; else hint="y/N"; fi
    while true; do
        if ! read_input "  ${prompt} [${hint}]: "; then
            die "No interactive terminal available for prompts"
        fi
        ans="${REPLY_LINE:-$def}"
        case "${ans,,}" in
            y|yes) REPLY=y; return 0 ;;
            n|no)  REPLY=n; return 0 ;;
            *) echo "  Please answer y or n." ;;
        esac
    done
}

# Current [homekit] / [matter] flags → homekit | google | both | empty.
voice_product_default() {
    local hk gh
    hk="$(conf_get homekit enabled)"
    gh="$(conf_get matter enabled)"
    hk="${hk,,}"
    gh="${gh,,}"
    if [[ "$hk" == "true" && "$gh" == "true" ]]; then
        printf '%s' both
    elif [[ "$hk" == "true" ]]; then
        printf '%s' homekit
    elif [[ "$gh" == "true" ]]; then
        printf '%s' google
    fi
}

# Sets REPLY to homekit | google | both | neither.
# $1 default (homekit|google|both|neither or empty). $2 "y" to offer Neither.
ask_voice_product() {
    local def="${1:-}" allow_off="${2:-}" hint ans prompt
    echo
    echo "  ${C_BOLD}Which voice control?${C_RESET}"
    echo "  ${C_CYAN}1${C_RESET}  Apple HomeKit / Siri"
    echo "  ${C_CYAN}2${C_RESET}  Google Home / Gemini"
    echo "  ${C_CYAN}3${C_RESET}  Both"
    if [[ "$allow_off" == "y" ]]; then
        echo "  ${C_CYAN}4${C_RESET}  Neither"
    fi
    echo
    case "$def" in
        homekit) hint=1 ;;
        google) hint=2 ;;
        both) hint=3 ;;
        neither) hint=4 ;;
        *) hint="" ;;
    esac
    if [[ -n "$hint" ]]; then
        prompt="  ${C_BOLD}Select${C_RESET} [${hint}]: "
    elif [[ "$allow_off" == "y" ]]; then
        prompt="  ${C_BOLD}Select${C_RESET} [1-4]: "
    else
        prompt="  ${C_BOLD}Select${C_RESET} [1-3]: "
    fi
    while true; do
        if ! read_input "$prompt"; then
            die "No interactive terminal available for prompts"
        fi
        if [[ -n "$hint" ]]; then
            ans="${REPLY_LINE:-$hint}"
        else
            ans="${REPLY_LINE}"
        fi
        case "${ans,,}" in
            1|homekit|siri) REPLY=homekit; return 0 ;;
            2|google|gemini) REPLY=google; return 0 ;;
            3|both) REPLY=both; return 0 ;;
            4|neither|off|none)
                if [[ "$allow_off" == "y" ]]; then
                    REPLY=neither
                    return 0
                fi
                ;;
        esac
        if [[ "$allow_off" == "y" ]]; then
            echo "  Enter 1, 2, 3, or 4."
        else
            echo "  Enter 1, 2, or 3."
        fi
    done
}

pause_enter() {
    read_input "  ${C_DIM}Press Enter to continue…${C_RESET} " || true
}

run_as_user() {
    sudo -u "$USERNAME" -H -- "$@"
}

# Run a bash script as USERNAME. The script body is this function's stdin.
# Do not pass that body as sudo's stdin: sudo's pty (use_pty) swallows it, and
# ssh inside the script then reads the script text as the password.
run_user_bash() {
    local body rc=0
    body="$(mktemp)"
    cat >"$body"
    if [[ -n "${USERNAME:-}" ]]; then
        chown "$USERNAME" "$body" 2>/dev/null || true
    fi
    if [[ "$(stat -c '%U' "$body" 2>/dev/null || echo root)" != "${USERNAME:-root}" ]]; then
        chmod 755 "$body"
    else
        chmod 700 "$body"
    fi
    run_as_user "$@" bash "$body" || rc=$?
    rm -f "$body"
    return "$rc"
}

# Run git as the install user (public HTTPS — no credentials).
run_git_as_user() {
    run_as_user git "$@"
}

# Keep origin on the public HTTPS URL (retargets old SSH / renamed remotes).
ensure_git_remote() {
    [[ -d "$SCCS_HOME/.git" ]] || return 0
    local current desired
    desired="$REPO_URL"
    current="$(run_as_user git -C "$SCCS_HOME" remote get-url origin 2>/dev/null || true)"
    [[ -n "$current" ]] || return 0
    if [[ "$current" != "$desired" ]]; then
        info "Updating origin: $current → $desired"
        run_as_user git -C "$SCCS_HOME" remote set-url origin "$desired"
        ok "origin set to $desired"
    fi
}

require_root() {
    if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
        echo
        fail "This script must be run as root."
        echo "  ${C_DIM}Try:${C_RESET}  ${C_BOLD}sudo $0 $*${C_RESET}"
        echo
        exit 1
    fi
}

show_context() {
    echo "  ${C_DIM}User${C_RESET}     ${C_BOLD}${USERNAME}${C_RESET}"
    echo "  ${C_DIM}Install${C_RESET}  ${C_BOLD}${SCCS_HOME}${C_RESET}"
    echo "  ${C_DIM}LAN IP${C_RESET}   ${C_BOLD}${LAN_ADDR}${C_RESET}"
    echo "  ${C_DIM}Service${C_RESET}  ${C_BOLD}${SERVICE_NAME}.service${C_RESET}"
    if [[ -f "$CONF" ]]; then
        echo "  ${C_DIM}Config${C_RESET}   ${C_GREEN}present${C_RESET}"
    else
        echo "  ${C_DIM}Config${C_RESET}   ${C_YELLOW}missing${C_RESET}  ${C_DIM}(created on install)${C_RESET}"
    fi
    echo
}

# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------
conf_get() {
    local section="$1" key="$2"
    [[ -f "$CONF" ]] || { echo ""; return 0; }
    python3 - "$CONF" "$section" "$key" <<'PY'
import configparser, sys
path, section, key = sys.argv[1:4]
c = configparser.ConfigParser()
c.read(path)
print(c.get(section, key, fallback="").strip())
PY
}

conf_set() {
    local section="$1"
    shift
    [[ -f "$CONF" ]] || die "No config at $CONF — run Install SCCS first"
    python3 - "$CONF" "$section" "$@" <<'PY'
import re, sys

def section_span(text, section):
    header = re.compile(rf"(?m)^\[{re.escape(section)}\][ \t]*\r?\n")
    m = header.search(text)
    if not m:
        return None
    start = m.start()
    nxt = re.compile(r"(?m)^\[[^\]]+\][ \t]*\r?\n")
    m2 = nxt.search(text, m.end())
    end = m2.start() if m2 else len(text)
    return start, end

path = sys.argv[1]
section = sys.argv[2]
pairs = list(zip(sys.argv[3::2], sys.argv[4::2]))
text = open(path, encoding="utf-8").read()
span = section_span(text, section)
if not span:
    sys.exit(f"section [{section}] not found in {path}")
start, end = span
block = text[start:end]
for key, value in pairs:
    if value is None:
        continue
    # Spaces/tabs only — \s would eat newlines and the next key.
    # Lambda replacement so MAC ":" is never a re escape.
    pat = re.compile(rf"(?m)^({re.escape(key)}[ \t]*=[ \t]*)(.*)$")
    if not pat.search(block):
        sys.exit(f"key {key} not found in [{section}]")
    block = pat.sub(lambda mo, v=value: mo.group(1) + v, block, count=1)
text = text[:start] + block + text[end:]
open(path, "w", encoding="utf-8").write(text)
PY
    chown "$USERNAME":www-data "$CONF" 2>/dev/null || true
}

normalize_mac() {
    local raw="${1// /}"
    raw="${raw,,}"
    if [[ "$raw" =~ ^([0-9a-f]{2}:){5}[0-9a-f]{2}$ ]]; then
        printf '%s' "$raw"; return 0
    fi
    if [[ "$raw" =~ ^[0-9a-f]{12}$ ]]; then
        printf '%s' "${raw:0:2}:${raw:2:2}:${raw:4:2}:${raw:6:2}:${raw:8:2}:${raw:10:2}"
        return 0
    fi
    return 1
}

normalize_victron_key() {
    local raw="${1// /}"
    raw="${raw,,}"
    raw="${raw#0x}"
    [[ "$raw" =~ ^[0-9a-f]{32}$ ]] || return 1
    printf '%s' "$raw"
}

require_checkout() {
    [[ -f "$SCCS_HOME/app.py" && -f "$SCCS_HOME/requirements.txt" ]] \
        || die "SCCS checkout not found at $SCCS_HOME — choose Install SCCS first"
    CONF="$SCCS_HOME/config/sccs.conf"
    CONF_DIST="$SCCS_HOME/config/sccs.conf.dist"
    [[ -f "$CONF_DIST" ]] || die "Missing $CONF_DIST"
}

require_conf() {
    require_checkout
    [[ -f "$CONF" ]] || die "Missing $CONF — choose Install SCCS first"
}

# Device-tree model, or empty when this is not a Pi.
pi_model() {
    local model=""
    if [[ -r /proc/device-tree/model ]]; then
        model="$(tr -d '\0' </proc/device-tree/model 2>/dev/null || true)"
    fi
    printf '%s' "$model"
}

# True when running on a Raspberry Pi (any model).
is_raspberry_pi() {
    local model
    model="$(pi_model)"
    [[ "${model,,}" == *"raspberry pi"* ]]
}

# True when running on a Raspberry Pi 5 (device-tree model string).
# "Raspberry Pi 500" matches too — same RP1 UART overlays.
is_raspberry_pi5() {
    local model
    model="$(pi_model)"
    [[ "${model,,}" == *"raspberry pi 5"* ]]
}

# config.txt section + overlays in the order the bootloader must apply them.
# Pi 4 ttyAMA numbers follow probe order (ttyAMA0 already exists), so uart2,
# uart3, uart4 become ttyAMA1 (GPS), ttyAMA2 (ESP32-1), ttyAMA3 (ESP32-2).
# Inserting these at the top of the section reverses that order.
uart_config_section() {
    if is_raspberry_pi5; then
        printf 'pi5\n'
    else
        printf 'pi4\n'
    fi
}

# Lines are "overlay device". Device nodes are the SCCS host UARTs.
host_uart_overlays() {
    if is_raspberry_pi5; then
        printf '%s %s\n' uart1-pi5 /dev/ttyAMA1
        printf '%s %s\n' uart2-pi5 /dev/ttyAMA2
        printf '%s %s\n' uart3-pi5 /dev/ttyAMA3
    else
        printf '%s %s\n' uart2 /dev/ttyAMA1
        printf '%s %s\n' uart3 /dev/ttyAMA2
        printf '%s %s\n' uart4 /dev/ttyAMA3
    fi
}

# Basename of the tty's device-tree node (serial@38000, serial@7e201600, …).
uart_of_name() {
    local dev="$1" path
    path="/sys/class/tty/${dev#/dev/}/device/of_node"
    [[ -e "$path" ]] || return 1
    basename "$(readlink -f "$path")"
}

# Prints 1 when GPIO26 is high against an internal pulldown, else 0.
# Returns 1 when the pin cannot be read.
sccs_detect_level() {
    local gpio="${SCCS_DETECT_GPIO:-26}" level=""
    if command -v pinctrl >/dev/null 2>&1; then
        if pinctrl set "$gpio" ip pd >/dev/null 2>&1; then
            sleep 0.02
            level="$(pinctrl lev "$gpio" 2>/dev/null | tr -cd '0-9' || true)"
            # Release the pad. Pulldown matches an unused header pin.
            pinctrl set "$gpio" no pd >/dev/null 2>&1 || true
            case "$level" in
                0|1) printf '%s' "$level"; return 0 ;;
            esac
        fi
    fi
    if ! python3 -c 'import lgpio' >/dev/null 2>&1; then
        return 1
    fi
    python3 - "$gpio" <<'PY'
import sys, time
import lgpio
gpio = int(sys.argv[1])
for chip in (0, 4):
    try:
        handle = lgpio.gpiochip_open(chip)
    except Exception:
        continue
    try:
        if lgpio.gpio_claim_input(handle, gpio, lgpio.SET_PULL_DOWN) < 0:
            continue
        time.sleep(0.02)
        level = lgpio.gpio_read(handle, gpio)
        if level < 0:
            continue
        sys.stdout.write(str(int(level)))
        sys.exit(0)
    finally:
        try:
            lgpio.gpio_free(handle, gpio)
        except Exception:
            pass
        lgpio.gpiochip_close(handle)
sys.exit(1)
PY
}

# Cached for this run: 1 = Core present, 0 = not present or unreadable.
sccs_core_detected() {
    if [[ -n "${SCCS_CORE_DETECT:-}" ]]; then
        [[ "$SCCS_CORE_DETECT" == "1" ]]
        return
    fi
    SCCS_CORE_DETECT=0
    is_raspberry_pi || return 1
    local level=""
    level="$(sccs_detect_level 2>/dev/null)" || return 1
    [[ "$level" == "1" ]] || return 1
    SCCS_CORE_DETECT=1
    return 0
}

# When the Core pull-up is present, confirm and continue. Otherwise ask $1.
# $2 is the default (y/n) for the question used when the Core is not detected.
ask_sccs_core_flash() {
    local unknown_prompt="$1" unknown_def="${2:-n}"
    if sccs_core_detected; then
        info "GPIO${SCCS_DETECT_GPIO} is high — the SCCS Core presence pull-up is there."
        ask_yn "SCCS Core detected. Proceed?" y
        return 0
    fi
    ask_yn "$unknown_prompt" "$unknown_def"
}

# True when /dev/ttyAMA2 and /dev/ttyAMA3 are the SCCS ESP UARTs, not just
# any PL011 that happened to get those names.
# Pi 5: UART2 (GPIO 4/5) is serial@38000, UART3 (GPIO 8/9) is serial@3c000.
# Pi 4: UART3 (GPIO 4/5) is serial@7e201600, UART4 (GPIO 8/9) is serial@7e201800.
esp_uarts_ready() {
    [[ -e /dev/ttyAMA2 && -e /dev/ttyAMA3 ]] || return 1
    local a2 a3
    a2="$(uart_of_name /dev/ttyAMA2)" || { UARTS_LIVE=1; return 0; }
    a3="$(uart_of_name /dev/ttyAMA3)" || { UARTS_LIVE=1; return 0; }
    if is_raspberry_pi5; then
        [[ "$a2" == *38000 && "$a3" == *3c000 ]] || return 1
    else
        [[ "$a2" == *201600 && "$a3" == *201800 ]] || return 1
    fi
    UARTS_LIVE=1
    return 0
}

# Give the install user access to a tty that udev may not have labelled yet.
grant_uart_node() {
    local node="$1"
    [[ -e "$node" ]] || return 0
    chgrp dialout "$node" 2>/dev/null || true
    chmod 660 "$node" 2>/dev/null || true
}

# Load the SCCS UART overlays into the running kernel.
# Returns 0 when the ESP ports exist and point at the right UARTs.
# Pi 4's PL011 is an AMBA device: a runtime overlay often creates no ttyAMA
# node, and when it does the index can follow probe order instead of the UART
# number. The of_node check rejects that. A wrong runtime overlay is removed
# so the boot config is what the next restart applies.
activate_host_uarts() {
    if esp_uarts_ready; then
        return 0
    fi
    command -v dtoverlay >/dev/null 2>&1 || return 1
    is_raspberry_pi || return 1

    local overlay node err
    local -a loaded=()
    while read -r overlay node; do
        [[ -n "$overlay" ]] || continue
        [[ -e "$node" ]] && continue
        info "Bringing up ${node} (${overlay})…"
        if ! err="$(dtoverlay "$overlay" 2>&1)"; then
            warn "dtoverlay ${overlay} failed${err:+: ${err}}"
        else
            loaded+=("$overlay")
        fi
    done < <(host_uart_overlays)

    if command -v udevadm >/dev/null 2>&1; then
        udevadm settle --timeout=5 || true
    fi

    local i
    for i in 1 2 3 4 5 6 7 8 9 10; do
        if esp_uarts_ready; then
            grant_uart_node /dev/ttyAMA1
            grant_uart_node /dev/ttyAMA2
            grant_uart_node /dev/ttyAMA3
            ok "ESP serial ports are live (/dev/ttyAMA2, /dev/ttyAMA3)"
            return 0
        fi
        sleep 0.2
    done

    if [[ ${#loaded[@]} -gt 0 && ( -e /dev/ttyAMA2 || -e /dev/ttyAMA3 ) ]]; then
        warn "Runtime UART load did not create the SCCS ESP ports. Removing it."
        # Post-decrement in (( ri-- )) is false when ri is 0, which trips set -e.
        local ri=${#loaded[@]}
        while (( ri > 0 )); do
            ri=$((ri - 1))
            dtoverlay -r "${loaded[$ri]}" >/dev/null 2>&1 || true
        done
    fi
    return 1
}

# Persist the running installer and continue after the restart that creates
# the UART devices. phase is "after-uart" (rest of a full install) or "esp".
save_install_resume() {
    local phase="$1" src
    mkdir -p "$INSTALL_RESUME_DIR"
    src="$(readlink -f "${BASH_SOURCE[0]}")"
    cp -a "$src" "$INSTALL_RESUME_SCRIPT"
    chmod 755 "$INSTALL_RESUME_SCRIPT"
    cat >"$INSTALL_RESUME_FILE" <<EOF
PHASE=${phase}
USERNAME=${USERNAME}
SCCS_HOME=${SCCS_HOME}
EOF
    chmod 644 "$INSTALL_RESUME_FILE"
    # Login hook only — not a boot service. The install is interactive, so it
    # has to run on the terminal that appears when someone SSHes in.
    cat >"$INSTALL_RESUME_PROFILE" <<EOF
# Created by the SCCS installer. Removed when the install continues.
case \$- in
  *i*) ;;
  *) return ;;
esac
[ -t 1 ] || return
[ "\$(id -un)" = "${USERNAME}" ] || return
[ -f ${INSTALL_RESUME_FILE} ] || return
[ -x ${INSTALL_RESUME_SCRIPT} ] || return
exec 9>>${INSTALL_RESUME_LOCK}
if ! flock -n 9; then
  echo "SCCS install is already running in another session."
  return
fi
echo
echo "SCCS install is continuing in this session."
sudo ${INSTALL_RESUME_SCRIPT}
exec 9>&-
EOF
    chmod 644 "$INSTALL_RESUME_PROFILE"
    cat >"$INSTALL_RESUME_SUDOERS" <<EOF
${USERNAME} ALL=(root) NOPASSWD: ${INSTALL_RESUME_SCRIPT}
EOF
    chmod 440 "$INSTALL_RESUME_SUDOERS"
    if ! visudo -cf "$INSTALL_RESUME_SUDOERS" >/dev/null 2>&1; then
        rm -f "$INSTALL_RESUME_SUDOERS"
    fi
}

clear_install_resume() {
    rm -f "$INSTALL_RESUME_FILE" "$INSTALL_RESUME_SCRIPT" "$INSTALL_RESUME_PROFILE" \
        "$INSTALL_RESUME_SUDOERS" "$INSTALL_RESUME_LOCK"
    SCCS_RESUME_PHASE=""
}

# Read /var/lib/sccs/install-resume if a previous run is waiting on a restart.
# Exports USERNAME and SCCS_HOME for resolve_identity. Returns 0 when a
# known phase was loaded.
load_install_resume() {
    SCCS_RESUME_PHASE=""
    [[ -f "$INSTALL_RESUME_FILE" ]] || return 1
    local phase="" user="" home="" line
    while IFS= read -r line || [[ -n "$line" ]]; do
        case "$line" in
            PHASE=*) phase="${line#PHASE=}" ;;
            USERNAME=*) user="${line#USERNAME=}" ;;
            SCCS_HOME=*) home="${line#SCCS_HOME=}" ;;
        esac
    done <"$INSTALL_RESUME_FILE"
    case "$phase" in
        after-uart|esp) ;;
        *) return 1 ;;
    esac
    if [[ -n "$user" ]] && id "$user" &>/dev/null; then
        USERNAME="$user"
    fi
    if [[ -n "$home" && -d "$home" ]]; then
        SCCS_HOME="$home"
    fi
    SCCS_RESUME_PHASE="$phase"
    return 0
}

# The ESP ports are still missing after a runtime overlay load. Ask to
# restart so the bootloader applies config.txt, then continue this install.
# Returns 1 if the user declines. Does not return on a successful reboot.
offer_restart_for_uarts() {
    local phase="$1"
    if ! is_raspberry_pi; then
        warn "ESP serial ports are missing, and this machine is not a Raspberry Pi"
        return 1
    fi
    echo
    warn "The ESP serial ports are not active on this boot."
    info "The installer enabled the Pi UART overlays. /dev/ttyAMA2 and /dev/ttyAMA3 appear when those overlays load, which on this board happens at startup."
    info "After the restart, SSH in as ${USERNAME}. The installer continues in that session."
    echo
    ask_yn "Restart now so the ESP32s can be detected?" y
    if [[ "$REPLY" != "y" ]]; then
        return 1
    fi
    save_install_resume "$phase"
    ok "Restarting. SSH back in as ${USERNAME} and the install continues on that terminal."
    echo "    ${C_DIM}If it does not start: sudo ${INSTALL_RESUME_SCRIPT}${C_RESET}"
    sync
    if ! systemctl reboot; then
        warn "Could not restart automatically. Restart the Pi, then SSH in as ${USERNAME}."
        exit 1
    fi
    exit 0
}

# Start sccs.service if the unit exists and it is not already active.
ensure_sccs_service_running() {
    if ! systemctl cat "${SERVICE_NAME}.service" &>/dev/null; then
        return 0
    fi
    if systemctl is-active --quiet "${SERVICE_NAME}.service" 2>/dev/null; then
        return 0
    fi
    info "Starting ${SERVICE_NAME}.service…"
    if systemctl start "${SERVICE_NAME}.service"; then
        ok "Service started"
    else
        warn "Could not start ${SERVICE_NAME}.service — nginx will 502 until it is running"
    fi
}

# The app reads sccs.conf once at process start. A step that changes that
# file has to restart the unit or the running process keeps the old values.
restart_sccs_service() {
    local why="${1:-}"
    local detail=""
    [[ -n "$why" ]] && detail=" ${why}"
    if systemctl restart "${SERVICE_NAME}.service"; then
        ok "Restarted ${SERVICE_NAME}.service${detail}"
    else
        warn "Could not restart ${SERVICE_NAME}.service${detail} — journalctl -u ${SERVICE_NAME}.service -b --no-pager"
    fi
}

# No-op when the unit is not installed yet (fresh install, before step_service)
# or when it is stopped. The next start loads the file.
restart_sccs_service_if_active() {
    if ! systemctl is-active --quiet "${SERVICE_NAME}.service" 2>/dev/null; then
        return 0
    fi
    restart_sccs_service "${1:-}"
}

# Set by conf_upsert_screen / conf_remove_screen. step_screens restarts once
# on the way out, after every panel in that step has been written.
SCREEN_CONFIG_CHANGED=0

restart_sccs_if_screen_config_changed() {
    if [[ "${SCREEN_CONFIG_CHANGED:-0}" -eq 1 ]]; then
        restart_sccs_service_if_active "to load touchscreen config"
        SCREEN_CONFIG_CHANGED=0
    fi
}

# Append dtoverlay=<name> at the end of [section] if it is not already
# present as a line-start assignment anywhere in config.txt.
# Appending keeps boot order: inserting under the header reverses it, and on
# Pi 4 the ttyAMA index follows that probe order.
# Returns 0 if the file was changed.
ensure_dtoverlay_in_section() {
    # ${ov} is not visible in the same local statement under set -u.
    local section="$1" ov="$2" line
    line="dtoverlay=${ov}"
    [[ -n "${CONFIG_TXT:-}" && -f "$CONFIG_TXT" ]] || return 1
    if grep -qE "^dtoverlay=${ov}([[:space:]]|,|$)" "$CONFIG_TXT"; then
        info "Already present: ${line}"
        return 1
    fi
    if grep -qE "^\[${section}\]" "$CONFIG_TXT"; then
        awk -v sec="[${section}]" -v line="$line" '
            BEGIN { insec = 0; done = 0 }
            /^\[/ {
                if (insec && !done) { print line; done = 1 }
                insec = ($0 == sec)
                print
                next
            }
            { print }
            END {
                if (!done) {
                    if (!insec) print sec
                    print line
                }
            }
        ' "$CONFIG_TXT" >"${CONFIG_TXT}.sccs.tmp" && mv "${CONFIG_TXT}.sccs.tmp" "$CONFIG_TXT"
    else
        printf '\n[%s]\n%s\n' "$section" "$line" >>"$CONFIG_TXT"
    fi
    ok "Enabled ${line} under [${section}]"
    return 0
}

# Write the SCCS UART overlays into config.txt. Returns 0 if the file changed.
configure_uart_overlays() {
    [[ -n "${CONFIG_TXT:-}" && -f "$CONFIG_TXT" ]] || return 1
    local section overlay node any=1
    section="$(uart_config_section)"
    while read -r overlay node; do
        [[ -n "$overlay" ]] || continue
        if ensure_dtoverlay_in_section "$section" "$overlay"; then
            any=0
            NEED_REBOOT=1
        fi
    done < <(host_uart_overlays)
    return "$any"
}

# Ensure key=value exists under [section] in $CONFIG_TXT. Returns 0 if changed.
ensure_boot_config_kv() {
    local section="$1" key="$2" value="$3" line="${2}=${3}"
    [[ -n "${CONFIG_TXT:-}" && -f "$CONFIG_TXT" ]] || return 1

    if grep -qE "^${key}=${value}$" "$CONFIG_TXT"; then
        info "Already present: ${line}"
        return 1
    fi

    if grep -qE "^${key}=" "$CONFIG_TXT"; then
        # Update any existing assignment (section-agnostic — power keys are global).
        sed -i -E "s|^${key}=.*$|${line}|" "$CONFIG_TXT"
        ok "Updated ${line} in ${CONFIG_TXT}"
        return 0
    fi

    if grep -qE "^\[${section}\]" "$CONFIG_TXT"; then
        awk -v sec="[${section}]" -v line="$line" '
            BEGIN { done = 0 }
            $0 == sec {
                print
                if (!done) { print line; done = 1 }
                next
            }
            { print }
            END {
                if (!done) {
                    print sec
                    print line
                }
            }
        ' "$CONFIG_TXT" >"${CONFIG_TXT}.sccs.tmp" && mv "${CONFIG_TXT}.sccs.tmp" "$CONFIG_TXT"
    else
        printf '\n[%s]\n%s\n' "$section" "$line" >>"$CONFIG_TXT"
    fi
    ok "Enabled ${line} under [${section}]"
    return 0
}

# Pi 5 powered via the PCB 5V rail (not USB-C PD) cannot negotiate 5A.
# Force a 5A PSU declaration in bootloader EEPROM + full USB current in config.txt.
ensure_pi5_5a_power() {
    info "Raspberry Pi 5 detected — power is on the 5V pin (no USB-PD negotiation)"

    local changed=0
    if [[ -n "${CONFIG_TXT:-}" && -f "$CONFIG_TXT" ]]; then
        # Full USB bus current (1.6A) instead of the 600mA non-PD default.
        if ensure_boot_config_kv pi5 usb_max_current_enable 1; then
            changed=1
            NEED_REBOOT=1
        fi
    else
        warn "config.txt not found — cannot set usb_max_current_enable"
    fi

    if ! command -v rpi-eeprom-config >/dev/null 2>&1; then
        warn "rpi-eeprom-config not found — cannot set PSU_MAX_CURRENT=5000"
        warn "Install raspberrypi-eeprom (or reboot after packages) and re-run install"
        return 0
    fi

    local current_psu
    current_psu="$(rpi-eeprom-config 2>/dev/null | grep -E '^PSU_MAX_CURRENT=' | tail -n1 || true)"
    if [[ "$current_psu" == "PSU_MAX_CURRENT=5000" ]]; then
        info "Bootloader EEPROM already has PSU_MAX_CURRENT=5000"
        [[ "$changed" -eq 1 ]] && ok "Pi 5 5A power config updated (reboot required)" \
            || ok "Pi 5 5A power config already applied"
        return 0
    fi

    local eeprom_conf
    eeprom_conf="$(mktemp)"
    if ! rpi-eeprom-config >"$eeprom_conf" 2>/dev/null; then
        rm -f "$eeprom_conf"
        warn "Could not read bootloader EEPROM config — skipped PSU_MAX_CURRENT"
        return 0
    fi

    if grep -qE '^PSU_MAX_CURRENT=' "$eeprom_conf"; then
        sed -i -E 's/^PSU_MAX_CURRENT=.*$/PSU_MAX_CURRENT=5000/' "$eeprom_conf"
    else
        # Keep a trailing newline; append the override.
        printf 'PSU_MAX_CURRENT=5000\n' >>"$eeprom_conf"
    fi

    if rpi-eeprom-config --apply "$eeprom_conf"; then
        ok "Bootloader EEPROM: PSU_MAX_CURRENT=5000 (scheduled — reboot applies it)"
        NEED_REBOOT=1
    else
        warn "Failed to apply PSU_MAX_CURRENT=5000 via rpi-eeprom-config"
    fi
    rm -f "$eeprom_conf"
}

# ---------------------------------------------------------------------------
# Identity / paths
# ---------------------------------------------------------------------------
resolve_identity() {
    # Prefer explicit USERNAME=, then the user who invoked sudo (whoami of the real session).
    if [[ -z "${USERNAME:-}" ]]; then
        if [[ -n "${SUDO_USER:-}" && "${SUDO_USER}" != "root" ]]; then
            USERNAME="$SUDO_USER"
        elif [[ -n "${LOGNAME:-}" && "${LOGNAME}" != "root" ]]; then
            USERNAME="$LOGNAME"
        else
            die "Set USERNAME to your login (e.g. sudo env USERNAME=\$(whoami) $0)"
        fi
    fi
    id "$USERNAME" &>/dev/null || die "User '$USERNAME' does not exist"

    USER_HOME="$(getent passwd "$USERNAME" | cut -d: -f6)"
    # Always install under the user's home unless SCCS_HOME is set explicitly.
    # Do not follow the script's location — bootstrap may be curl|bash or a
    # downloaded copy in /tmp; the real tree lives at ~/sccs.
    SCCS_HOME="${SCCS_HOME:-$USER_HOME/sccs}"

    # Optional: directory of this script when run as a real file (for VERSION
    # before clone). Empty when piped via curl|bash.
    SCRIPT_DIR=""
    local src="${BASH_SOURCE[0]:-}"
    if [[ -n "$src" && -f "$src" ]]; then
        local dir
        dir="$(cd "$(dirname "$src")" && pwd)"
        case "$dir" in
            /dev|/dev/*|/proc|/proc/*) SCRIPT_DIR="" ;;
            *) SCRIPT_DIR="$dir" ;;
        esac
    fi

    CONF="$SCCS_HOME/config/sccs.conf"
    CONF_DIST="$SCCS_HOME/config/sccs.conf.dist"
    CONFIG_TXT=""
    for candidate in /boot/firmware/config.txt /boot/config.txt; do
        [[ -f "$candidate" ]] && { CONFIG_TXT="$candidate"; break; }
    done
}

# ===========================================================================
# Steps
# ===========================================================================

step_packages() {
    step_begin "System packages"
    info "apt update + upgrade (this can take a while)…"
    apt-get update -y
    DEBIAN_FRONTEND=noninteractive apt-get upgrade -y
    info "Installing required packages…"
    DEBIAN_FRONTEND=noninteractive apt-get install -y \
        nginx samba samba-common-bin \
        python3-venv python3-lgpio \
        git network-manager nftables \
        usbmuxd libimobiledevice-utils ipheth-utils \
        bluez curl
    ok "Packages installed"
}

step_groups_and_dirs() {
    step_begin "User groups and project directory"
    mkdir -p "$SCCS_HOME"
    for g in "${REQUIRED_GROUPS[@]}"; do
        if getent group "$g" >/dev/null 2>&1; then
            if id -nG "$USERNAME" | tr ' ' '\n' | grep -qx "$g"; then
                info "Already in group: $g"
            else
                usermod -aG "$g" "$USERNAME"
                ok "Added $USERNAME to $g"
                GROUPS_CHANGED=1
                NEED_REBOOT=1
            fi
        else
            warn "Group '$g' not present on this OS — skipped"
        fi
    done
    chown -R "$USERNAME":www-data "$SCCS_HOME"
    find "$SCCS_HOME" -type d -exec chmod 2775 {} +
    find "$SCCS_HOME" -type f -exec chmod ug+rw {} + 2>/dev/null || true
    [[ -f "$SCCS_HOME/install.sh" ]] && chmod ug+x "$SCCS_HOME/install.sh" || true
    ok "Ownership ${USERNAME}:www-data on $SCCS_HOME"
    # nginx (www-data) must traverse $USER_HOME to serve $SCCS_HOME/static/.
    # o+x allows traverse only — not listing (home stays non-listable for others).
    if [[ -d "$USER_HOME" ]]; then
        local home_mode
        home_mode="$(stat -c '%a' "$USER_HOME" 2>/dev/null || echo '')"
        if [[ -n "$home_mode" && "$((8#$home_mode & 8#001))" -eq 0 ]]; then
            chmod o+x "$USER_HOME"
            ok "Added other-execute on $USER_HOME (nginx can reach static files)"
        else
            info "Home already traversable by others: $USER_HOME"
        fi
    fi
    if [[ "$GROUPS_CHANGED" -eq 1 ]]; then
        warn "New groups apply to new logins and to systemd after the next service start."
    fi
}

step_repo() {
    step_begin "Repository"
    if [[ -f "$SCCS_HOME/app.py" && -f "$SCCS_HOME/requirements.txt" ]]; then
        ok "Repository already present at $SCCS_HOME"
        if [[ -d "$SCCS_HOME/.git" ]]; then
            ensure_git_remote
            info "git remote: $(run_as_user git -C "$SCCS_HOME" remote get-url origin 2>/dev/null || echo unknown)"
        fi
    else
        info "Cloning $REPO_URL → $SCCS_HOME"
        mkdir -p "$SCCS_HOME"
        chown "$USERNAME":www-data "$SCCS_HOME" 2>/dev/null || chown "$USERNAME" "$SCCS_HOME"
        # Empty dir is fine (bootstrap: mkdir then run install from /tmp).
        # Non-empty non-checkout is a hard stop so we never clobber random files.
        if [[ -n "$(ls -A "$SCCS_HOME" 2>/dev/null || true)" ]]; then
            die "$SCCS_HOME is not empty and is not a SCCS checkout — move it aside or set SCCS_HOME"
        fi
        # GIT_TERMINAL_PROMPT=0: never hang on username/password prompts.
        if ! run_as_user env GIT_TERMINAL_PROMPT=0 git clone "$REPO_URL" "$SCCS_HOME"; then
            fail "git clone failed"
            info "Check network access to GitHub, then: git clone $REPO_URL $SCCS_HOME"
            die "Could not clone $REPO_URL"
        fi
        chown -R "$USERNAME":www-data "$SCCS_HOME"
        ok "Cloned repository"
    fi
    CONF="$SCCS_HOME/config/sccs.conf"
    CONF_DIST="$SCCS_HOME/config/sccs.conf.dist"
    [[ -f "$CONF_DIST" ]] || die "Missing $CONF_DIST — incomplete checkout?"
}

# Hidden prompt. Uses the controlling terminal so a mismatched confirmation
# can be re-entered without smbpasswd consuming the only attempt.
read_secret_tty() {
    local prompt="$1" __dest="$2" __val=""
    if ( : </dev/tty ) 2>/dev/null; then
        read -r -s -p "  ${prompt}" __val </dev/tty || return 1
    elif [[ -t 0 ]]; then
        read -r -s -p "  ${prompt}" __val || return 1
    else
        return 1
    fi
    echo
    printf -v "$__dest" '%s' "$__val"
}

# Ask twice and retry while the two entries differ. smbpasswd -s still
# reads the password twice; both lines are the confirmed value.
set_samba_password() {
    local pass="" confirm=""
    info "Enter the Samba share password twice. If they differ, you can try again."
    info "Leave both blank to skip."
    while true; do
        read_secret_tty "Samba password: " pass \
            || die "No interactive terminal available for prompts"
        read_secret_tty "Confirm Samba password: " confirm \
            || die "No interactive terminal available for prompts"
        if [[ -z "$pass" && -z "$confirm" ]]; then
            skip_note "Samba password not set — set later with: sudo smbpasswd -a $USERNAME"
            return 0
        fi
        if [[ "$pass" != "$confirm" ]]; then
            warn "Passwords do not match. Try again."
            continue
        fi
        # -s reads the new password and the confirmation from stdin.
        if printf '%s\n%s\n' "$pass" "$confirm" | smbpasswd -a -s "$USERNAME"; then
            pass=""
            confirm=""
            ok "Samba user configured — use it to open \\\\$(hostname -s)\\sccs"
            return 0
        fi
        pass=""
        confirm=""
        warn "smbpasswd could not store the password."
        ask_yn "Try again?" y
        if [[ "$REPLY" != "y" ]]; then
            skip_note "Samba password not set — set later with: sudo smbpasswd -a $USERNAME"
            return 0
        fi
    done
}

step_samba() {
    step_begin "Samba share [sccs]"
    # Always (re)write the share so force user matches the installing login (whoami / SUDO_USER).
    info "Installing [sccs] share for user ${C_BOLD}${USERNAME}${C_RESET} → $SCCS_HOME"
    python3 - "$SCCS_HOME" "$USERNAME" <<'PY'
import re, sys
path_home, user = sys.argv[1], sys.argv[2]
smb = "/etc/samba/smb.conf"
text = open(smb, encoding="utf-8").read()
# Drop any existing [sccs] section (until next [section] or EOF)
text = re.sub(
    r"(?ms)^\s*\[sccs\]\s*\n.*?(?=^\s*\[|\Z)",
    "",
    text,
)
block = f"""
[sccs]
    path = {path_home}
    writable = yes
    browsable = yes
    public = no
    valid users = {user}
    write list = {user}
    force user = {user}
    force group = www-data
    create mask = 0664
    force create mode = 0664
    directory mask = 0775
    force directory mode = 2775
    hide dot files = no
"""
if not text.endswith("\n"):
    text += "\n"
open(smb, "w", encoding="utf-8").write(text.rstrip() + "\n" + block)
print("wrote [sccs]")
PY
    ok "force user = $USERNAME  (force group = www-data)"
    testparm -s >/dev/null 2>&1 && ok "smb.conf syntax OK" || warn "testparm reported issues"

    echo
    info "Samba exposes the SCCS project as a network share so you can edit files"
    info "from another computer without SSH."
    info "  Share path:  ${C_BOLD}\\\\$(hostname -s)\\sccs${C_RESET}  or  ${C_BOLD}smb://$(hostname -s).local/sccs${C_RESET}"
    info "  Login user:  ${C_BOLD}${USERNAME}${C_RESET}"
    info "The Samba password is ${C_BOLD}separate${C_RESET} from the Linux login password —"
    info "smbpasswd only stores credentials for the share (not your SSH/sudo password)."
    info "Choose something you will use when mounting the share from another device."
    echo
    ask_yn "Set or update Samba password for '$USERNAME' now?" y
    if [[ "$REPLY" == "y" ]]; then
        set_samba_password
    else
        skip_note "Samba password not set — set later with: sudo smbpasswd -a $USERNAME"
    fi
    systemctl enable --now smbd nmbd >/dev/null 2>&1 || systemctl restart smbd nmbd
    ok "smbd/nmbd running"
}

step_nginx() {
    step_begin "nginx reverse proxy (port 80 → 5000)"
    cat >/etc/nginx/sites-available/sccs <<EOF
server {
    listen 80;
    server_name _;
    root $SCCS_HOME;

    # Text assets only. woff2 is already compressed, and proxied responses
    # (HTML and /assets/) are gzipped by Flask — gzip_proxied stays off.
    gzip on;
    gzip_vary on;
    gzip_comp_level 5;
    gzip_min_length 860;
    gzip_types
        text/css
        text/javascript
        application/javascript
        application/json
        image/svg+xml
        application/manifest+json;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 86400;
        proxy_send_timeout 86400;
        proxy_buffering off;
    }

    # Served from \$document_root/static/ (root above). Do not pair alias with
    # try_files — that combination often 404s. Requires o+x on the user home
    # so www-data can traverse to this tree (set in step_groups_and_dirs).
    # No long-lived browser cache — tree is small and UI assets change often.
    location /static/ {
        expires off;
        add_header Cache-Control "no-cache";
        access_log off;
    }
}
EOF
    ln -sfn /etc/nginx/sites-available/sccs /etc/nginx/sites-enabled/sccs
    rm -f /etc/nginx/sites-enabled/default
    nginx -t 2>/dev/null || die "nginx -t failed"
    systemctl enable nginx >/dev/null 2>&1 || true
    systemctl restart nginx
    ok "nginx configured and restarted"
}

step_venv() {
    step_begin "Python virtualenv and dependencies"
    if [[ ! -d "$SCCS_HOME/venv" ]]; then
        info "Creating venv with --system-site-packages (for python3-lgpio)…"
        run_as_user python3 -m venv --system-site-packages "$SCCS_HOME/venv"
        ok "venv created"
    else
        ok "venv already exists"
    fi
    info "pip install --upgrade (latest) -r requirements.txt…"
    run_as_user "$SCCS_HOME/venv/bin/pip" install --upgrade pip
    run_as_user "$SCCS_HOME/venv/bin/pip" install --upgrade --upgrade-strategy eager -r "$SCCS_HOME/requirements.txt"
    ok "Python packages installed"
}

step_config() {
    step_begin "Application config"
    if [[ ! -f "$CONF" ]]; then
        run_as_user cp "$CONF_DIST" "$CONF"
        ok "Created $CONF from sccs.conf.dist"
    else
        ok "Using $CONF"
    fi
    local secret
    secret="$(conf_get system secret_key)"
    if [[ -z "$secret" || "$secret" == "CHANGE_ME_TO_A_LONG_RANDOM_STRING" ]]; then
        conf_set system secret_key "$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
        ok "Generated system.secret_key"
        restart_sccs_service_if_active "to load the new secret key"
    else
        ok "secret_key already set"
    fi
    ensure_wifi_uplink_default
}

# Fresh installs prefer Wi-Fi. USB tethering stays available as the fallback.
ensure_wifi_uplink_default() {
    local pref="$SCCS_HOME/config/preferred_uplink.json"
    [[ -f "$pref" ]] && return 0
    run_as_user mkdir -p "$SCCS_HOME/config"
    printf '%s\n' '{
  "prefer": "wifi"
}
' | run_as_user tee "$pref" >/dev/null
    ok "Preferred internet is Wi-Fi"
}

# mode: optional (full install — may skip) | required (menu path — configure now)
w1_sensors_present() {
    local id
    for id in /sys/bus/w1/devices/28-*; do
        [[ -e "$id" ]] || return 1
        return 0
    done
    return 1
}

# GPIO ${W1_GPIO} is the DS18B20 bus. The overlay is written for the next boot
# and loaded now so the sensor step can see probes on a first install.
ensure_w1_bus() {
    modprobe wire 2>/dev/null || true
    modprobe w1-gpio 2>/dev/null || true
    modprobe w1-therm 2>/dev/null || true
    if w1_sensors_present; then
        return 0
    fi
    if [[ -n "${CONFIG_TXT:-}" && -f "$CONFIG_TXT" ]] && ! grep -qE '^dtoverlay=w1-gpio' "$CONFIG_TXT"; then
        local w1_line="dtoverlay=w1-gpio,gpiopin=${W1_GPIO}"
        if grep -qE '^\[all\]' "$CONFIG_TXT"; then
            awk -v line="$w1_line" '
                BEGIN { done=0 }
                /^\[all\]/ && !done { print; print line; done=1; next }
                { print }
                END { if (!done) print line }
            ' "$CONFIG_TXT" >"${CONFIG_TXT}.sccs.tmp" && mv "${CONFIG_TXT}.sccs.tmp" "$CONFIG_TXT"
        else
            printf '\n[all]\n%s\n' "$w1_line" >>"$CONFIG_TXT"
        fi
        ok "Enabled $w1_line"
        NEED_REBOOT=1
    fi
    if command -v dtoverlay >/dev/null 2>&1; then
        dtoverlay w1-gpio "gpiopin=${W1_GPIO}" >/dev/null 2>&1 || true
    fi
    local i
    for i in 1 2 3 4 5 6 7 8 9 10; do
        w1_sensors_present && return 0
        sleep 0.3
    done
    return 1
}

step_sensors() {
    local mode="${1:-optional}"
    step_begin "1-Wire temperature sensors"
    require_conf

    info "Bringing up the 1-Wire bus on GPIO ${W1_GPIO}…"
    ensure_w1_bus || true
    local scan_tries=0
    local -a W1_IDS=()
    while true; do
        mapfile -t W1_IDS < <(ls /sys/bus/w1/devices/ 2>/dev/null | grep '^28' || true)
        [[ ${#W1_IDS[@]} -gt 0 ]] && break
        warn "No DS18B20 devices under /sys/bus/w1/devices/"
        if (( scan_tries >= 2 )); then
            break
        fi
        ask_yn "Scan again for temperature sensors?" y
        if [[ "$REPLY" != "y" ]]; then
            break
        fi
        scan_tries=$((scan_tries + 1))
        ensure_w1_bus || true
        sleep 1
    done

    local cur_out cur_fridge cur_freezer
    cur_out="$(conf_get sensors outside_temp_sensor)"
    cur_fridge="$(conf_get sensors fridge_temp_sensor)"
    cur_freezer="$(conf_get sensors freezer_temp_sensor)"
    if [[ -n "$cur_out$cur_fridge$cur_freezer" ]]; then
        info "Current: ambient=${cur_out:-∅}  fridge=${cur_fridge:-∅}  freezer=${cur_freezer:-∅}"
    fi

    read_w1_temp() {
        local id="$1" raw t
        raw="$(cat "/sys/bus/w1/devices/${id}/w1_slave" 2>/dev/null || true)"
        if [[ "$raw" == *"YES"* ]]; then
            t="$(echo "$raw" | sed -n 's/.*t=\([-0-9]*\).*/\1/p' | tail -1)"
            if [[ -n "$t" ]]; then
                python3 -c "print(f'{int($t)/1000:.1f}°C')" 2>/dev/null || echo "${t} m°C"
                return
            fi
        fi
        echo "n/a"
    }

    local do_assign=0
    if [[ ${#W1_IDS[@]} -eq 0 ]]; then
        if [[ -z "$cur_out$cur_fridge$cur_freezer" ]]; then
            skip_note "1-Wire skipped — no sensors detected (configure later from the menu)"
            return 0
        fi
        info "Configured roles can still be deleted from sccs.conf"
    fi

    if [[ ${#W1_IDS[@]} -gt 0 ]]; then
        echo
        info "Detected ${#W1_IDS[@]} sensor(s):"
        local i id temp
        for i in "${!W1_IDS[@]}"; do
            id="${W1_IDS[$i]}"
            temp="$(read_w1_temp "$id")"
            printf "  ${C_BOLD}[%d]${C_RESET}  %s   ${C_CYAN}%s${C_RESET}\n" "$((i + 1))" "$id" "$temp"
        done
        echo
    fi

    if [[ "$mode" == "optional" ]]; then
        ask_yn "Configure temperature sensors now?" y
        [[ "$REPLY" == "y" ]] && do_assign=1
    else
        do_assign=1
    fi

    if [[ "$do_assign" -ne 1 ]]; then
        skip_note "1-Wire assignment skipped"
        return 0
    fi

    info "ambient → outside_temp_sensor · fridge → fridge_temp_sensor · freezer → freezer_temp_sensor"
    info "Assign writes that role. Skip leaves it. Delete clears this sensor from config."

    declare -A ROLE_FOR=()
    declare -A CLEAR_KEY=()
    local used_ambient=0 used_fridge=0 used_freezer=0 choice

    role_of_id() {
        local sid="$1"
        if [[ -n "$cur_out" && "$cur_out" == "$sid" ]]; then
            echo ambient
        elif [[ -n "$cur_fridge" && "$cur_fridge" == "$sid" ]]; then
            echo fridge
        elif [[ -n "$cur_freezer" && "$cur_freezer" == "$sid" ]]; then
            echo freezer
        fi
    }

    key_for_role() {
        case "$1" in
            ambient) echo outside_temp_sensor ;;
            fridge) echo fridge_temp_sensor ;;
            freezer) echo freezer_temp_sensor ;;
        esac
    }

    note_delete() {
        local role="$1"
        local key
        key="$(key_for_role "$role")"
        CLEAR_KEY["$key"]=1
        case "$role" in
            ambient) used_ambient=0; cur_out="" ;;
            fridge) used_fridge=0; cur_fridge="" ;;
            freezer) used_freezer=0; cur_freezer="" ;;
        esac
        unset 'ROLE_FOR[$role]'
    }

    local already
    for id in "${W1_IDS[@]}"; do
        temp="$(read_w1_temp "$id")"
        already="$(role_of_id "$id")"
        while true; do
            echo
            if [[ -n "$already" ]]; then
                echo "  Sensor ${C_BOLD}${id}${C_RESET} (${temp})  ${C_DIM}currently ${already}${C_RESET}"
            else
                echo "  Sensor ${C_BOLD}${id}${C_RESET} (${temp})"
            fi
            read -r -p "    [a] ambient  [f] fridge  [z] freezer  [s] skip  [d] delete  — " choice || true
            case "${choice,,}" in
                a|ambient)
                    [[ "$used_ambient" -eq 1 ]] && { warn "ambient already assigned"; continue; }
                    ROLE_FOR[ambient]="$id"; used_ambient=1
                    unset 'CLEAR_KEY[outside_temp_sensor]'
                    ok "ambient ← $id"; break ;;
                f|fridge)
                    [[ "$used_fridge" -eq 1 ]] && { warn "fridge already assigned"; continue; }
                    ROLE_FOR[fridge]="$id"; used_fridge=1
                    unset 'CLEAR_KEY[fridge_temp_sensor]'
                    ok "fridge ← $id"; break ;;
                z|freezer)
                    [[ "$used_freezer" -eq 1 ]] && { warn "freezer already assigned"; continue; }
                    ROLE_FOR[freezer]="$id"; used_freezer=1
                    unset 'CLEAR_KEY[freezer_temp_sensor]'
                    ok "freezer ← $id"; break ;;
                s|skip|"") info "skipped $id"; break ;;
                d|del|delete)
                    if [[ -z "$already" ]]; then
                        info "$id is not in config — skip to leave it unassigned"
                        continue
                    fi
                    note_delete "$already"
                    ok "deleted $already ($id) from config"
                    break ;;
                *) echo "    Enter a, f, z, s, or d." ;;
            esac
        done
    done

    # Configured IDs that are not on the bus can still be removed.
    local role cur
    for role in ambient fridge freezer; do
        case "$role" in
            ambient) cur="$cur_out" ;;
            fridge) cur="$cur_fridge" ;;
            freezer) cur="$cur_freezer" ;;
        esac
        [[ -n "$cur" ]] || continue
        local on_bus=0
        for id in "${W1_IDS[@]}"; do
            [[ "$id" == "$cur" ]] && { on_bus=1; break; }
        done
        [[ "$on_bus" -eq 0 ]] || continue
        while true; do
            echo
            echo "  ${C_BOLD}${role}${C_RESET} is ${cur}  ${C_DIM}not on the bus${C_RESET}"
            read -r -p "    [d] delete  [s] skip  — " choice || true
            case "${choice,,}" in
                d|del|delete)
                    note_delete "$role"
                    ok "deleted $role ($cur) from config"
                    break ;;
                s|skip|"") info "skipped $role"; break ;;
                *) echo "    Enter d or s." ;;
            esac
        done
    done

    local set_args=()
    local key
    if [[ ${#CLEAR_KEY[@]} -gt 0 ]]; then
        for key in "${!CLEAR_KEY[@]}"; do
            set_args+=("$key" "")
        done
    fi
    [[ -n "${ROLE_FOR[ambient]:-}" ]] && set_args+=(outside_temp_sensor "${ROLE_FOR[ambient]}")
    [[ -n "${ROLE_FOR[fridge]:-}" ]] && set_args+=(fridge_temp_sensor "${ROLE_FOR[fridge]}")
    [[ -n "${ROLE_FOR[freezer]:-}" ]] && set_args+=(freezer_temp_sensor "${ROLE_FOR[freezer]}")
    if [[ ${#set_args[@]} -gt 0 ]]; then
        conf_set sensors "${set_args[@]}"
        ok "Updated [sensors]"
        restart_sccs_service_if_active "to load temperature sensors"
    else
        info "No sensor roles chosen"
    fi
}

step_boot_firmware() {
    step_begin "Pi boot firmware (UART · 1-Wire · Bluetooth · power)"
    # Fixed SCCS mapping — no user choices
    if [[ -z "${CONFIG_TXT:-}" ]]; then
        warn "config.txt not found — cannot enable overlays automatically"
    else
        cp -a "$CONFIG_TXT" "${CONFIG_TXT}.bak.sccs.$(date +%Y%m%d%H%M%S)"
        local changed_cfg=0 w1_line

        # Same GPIOs on both boards; overlay *names* differ:
        #   Pi 4 (BCM2711): uart2=GPIO0/1  uart3=GPIO4/5  uart4=GPIO8/9
        #   Pi 5 (RP1):     uart1-pi5=GPIO0/1  uart2-pi5=GPIO4/5  uart3-pi5=GPIO8/9
        # Device nodes stay /dev/ttyAMA1 (GPS), ttyAMA2 (ESP1), ttyAMA3 (ESP2).
        # Do not enable ctsrts — GPIO3 is 1-Wire and GPIO7 is a reed input.
        if configure_uart_overlays; then
            changed_cfg=1
        fi

        w1_line="dtoverlay=w1-gpio,gpiopin=${W1_GPIO}"
        if ! grep -qE "dtoverlay=w1-gpio" "$CONFIG_TXT"; then
            if grep -qE '^\[all\]' "$CONFIG_TXT"; then
                awk -v line="$w1_line" '
                    BEGIN { done=0 }
                    /^\[all\]/ && !done { print; print line; done=1; next }
                    { print }
                    END { if (!done) print line }
                ' "$CONFIG_TXT" >"${CONFIG_TXT}.sccs.tmp" && mv "${CONFIG_TXT}.sccs.tmp" "$CONFIG_TXT"
            else
                printf '\n[all]\n%s\n' "$w1_line" >>"$CONFIG_TXT"
            fi
            ok "Enabled $w1_line"
            changed_cfg=1
            NEED_REBOOT=1
        else
            info "Already present: w1-gpio overlay"
        fi

        # config.txt is read at startup. Load the UARTs now so the ESP flash
        # in this same run can see /dev/ttyAMA2 and /dev/ttyAMA3.
        activate_host_uarts || true

        if [[ "$changed_cfg" -eq 0 ]]; then
            ok "Boot overlays already match SCCS defaults"
        elif esp_uarts_ready; then
            ok "Updated $CONFIG_TXT — ESP serial ports are live on this boot"
        else
            ok "Updated $CONFIG_TXT — restart required before the ESP serial ports exist"
        fi
    fi

    local cmdline="" before after
    for candidate in /boot/firmware/cmdline.txt /boot/cmdline.txt; do
        [[ -f "$candidate" ]] && { cmdline="$candidate"; break; }
    done
    if [[ -z "$cmdline" ]]; then
        warn "cmdline.txt not found"
    else
        before="$(cat "$cmdline")"
        after="$(echo "$before" | sed -E 's/\s*console=serial0,[0-9]+\s*/ /g; s/\s*console=ttyAMA0,[0-9]+\s*/ /g; s/  +/ /g; s/^ //; s/ $//')"
        if [[ "$before" != "$after" ]]; then
            cp -a "$cmdline" "${cmdline}.bak.sccs"
            printf '%s\n' "$after" >"$cmdline"
            ok "Freed primary UART for Bluetooth"
            NEED_REBOOT=1
        else
            ok "Primary UART already free for Bluetooth"
        fi
    fi

    # SCCS board feeds the Pi from the 5V rail, so USB-C PD never negotiates 5A.
    if is_raspberry_pi5; then
        ensure_pi5_5a_power
    else
        info "Not a Raspberry Pi 5 — skipping 5A power supply override"
    fi
}

ensure_arduino_cli() {
    export PATH="/usr/local/bin:${PATH}"
    if command -v arduino-cli >/dev/null 2>&1; then
        ok "arduino-cli: $(command -v arduino-cli)"
        return 0
    fi
    info "Installing arduino-cli to /usr/local/bin…"
    curl -fsSL https://raw.githubusercontent.com/arduino/arduino-cli/master/install.sh \
        | BINDIR=/usr/local/bin sh
    command -v arduino-cli >/dev/null 2>&1 || die "arduino-cli install failed"
    ok "arduino-cli installed"
}

# Returns: 0 = port found (ESP_PORT set), 1 = timeout/fail, 2 = user skip
# $1 = label, $2 = preferred host UART (e.g. /dev/ttyAMA2). The SCCS Core
# wires each ESP's UART0 to that Pi UART — same pins as the ROM bootloader.
# The ttyAMA node exists because the Pi UART overlay is loaded. Download mode
# does not create it, so a missing node fails immediately instead of asking
# the user to hold BOOT for three minutes.
wait_for_esp_port() {
    local label="$1" preferred="${2:-}" tries=0 max_tries=90 ans
    if [[ -n "$preferred" ]]; then
        if [[ -e "$preferred" ]]; then
            ESP_PORT="$preferred"
            ok "Found ${ESP_PORT} for ${label}" >&2
            return 0
        fi
        warn "${preferred} is not available, so ${label} cannot be detected" >&2
        info "That path is the Pi UART. It appears when the UART overlay loads." >&2
        return 1
    fi
    while (( tries < max_tries )); do
        mapfile -t ports < <(ls /dev/ttyACM* /dev/ttyUSB* 2>/dev/null | sort || true)
        if [[ ${#ports[@]} -ge 1 ]]; then
            ESP_PORT="${ports[0]}"
            ok "Found ${ESP_PORT} for ${label}" >&2
            return 0
        fi
        if (( tries == 0 )); then
            info "Waiting for serial port (download mode)…" >&2
            info "Type ${C_BOLD}s${C_RESET}${C_DIM} + Enter anytime to skip (no hardware / wrong step).${C_RESET}" >&2
        fi
        tries=$((tries + 1))
        if (( tries % 5 == 1 )); then
            info "No serial port yet — hold BOOT, tap RESET, release BOOT — or ${C_BOLD}s${C_RESET}${C_DIM} to skip.${C_RESET}" >&2
        fi
        # Interruptible wait: user can type s/skip/q while we poll
        ans=""
        if read -r -t 2 ans 2>/dev/null; then
            case "${ans,,}" in
                s|skip|q|quit|abort)
                    warn "Skip requested — leaving download wait for ${label}" >&2
                    return 2
                    ;;
            esac
        fi
    done
    warn "Timed out waiting for serial port for ${label}" >&2
    return 1
}

# True when the flashed app answers GETVCC on the host UART (not just the ROM).
verify_esp_protocol() {
    local port="$1"
    python3 - "$port" <<'PY'
import os, sys, termios, time, select

port = sys.argv[1]
try:
    fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
except OSError:
    sys.exit(1)
try:
    attrs = termios.tcgetattr(fd)
    iflag, oflag, cflag, lflag, ispeed, ospeed, cc = attrs
    cflag &= ~(termios.CSIZE | termios.PARENB | termios.CSTOPB | termios.CRTSCTS)
    cflag |= termios.CS8 | termios.CREAD | termios.CLOCAL
    lflag &= ~(termios.ICANON | termios.ECHO | termios.ECHOE | termios.ISIG)
    iflag &= ~(termios.IXON | termios.IXOFF | termios.IXANY | termios.INLCR | termios.ICRNL)
    oflag &= ~termios.OPOST
    cc[termios.VMIN] = 0
    cc[termios.VTIME] = 0
    attrs = [iflag, oflag, cflag, lflag, termios.B115200, termios.B115200, cc]
    termios.tcsetattr(fd, termios.TCSANOW, attrs)
    termios.tcflush(fd, termios.TCIOFLUSH)
    # App firmware needs a moment after RESET (ROM banner + Arduino setup).
    time.sleep(1.5)
    os.write(fd, b"GETVCC\n")
    deadline = time.time() + 2.0
    buf = b""
    while time.time() < deadline:
        ready, _, _ = select.select([fd], [], [], 0.2)
        if ready:
            try:
                chunk = os.read(fd, 256)
            except BlockingIOError:
                chunk = b""
            if chunk:
                buf += chunk
                # Require a real reply. "GETVCC" itself contains the letters VCC.
                if b"VCC " in buf or b"VCC\n" in buf or b"VCC\r" in buf:
                    sys.exit(0)
    sys.exit(1)
finally:
    os.close(fd)
PY
}

print_esp_download_steps() {
    echo "    1. Hold ${C_BOLD}BOOT${C_RESET} on both"
    echo "    2. Press and release ${C_BOLD}RESET${C_RESET} on both"
    echo "    3. Release ${C_BOLD}BOOT${C_RESET} on both"
}

print_esp_download_steps_one() {
    echo "    1. Hold ${C_BOLD}BOOT${C_RESET}"
    echo "    2. Press and release ${C_BOLD}RESET${C_RESET}"
    echo "    3. Release ${C_BOLD}BOOT${C_RESET}"
}

# Upload one sketch to a known port. On failure, re-prompt download mode for
# that module only. Returns 0 if firmware was uploaded, 1 if skipped/failed.
upload_esp_firmware() {
    local sketch_rel="$1" label="$2" port="$3"
    local sketch_dir="$SCCS_HOME/$sketch_rel" ans wait_rc
    [[ -d "$sketch_dir" ]] || { warn "Missing $sketch_dir"; return 1; }
    [[ -n "$port" ]] || { skip_note "${label} flash skipped (no serial port)"; return 1; }

    while true; do
        info "Uploading $sketch_rel → $port…"
        # Same user as compile — root's ~/.arduino15 does not have the ESP32 core.
        if run_as_user env PATH="$PATH" arduino-cli upload -p "$port" --fqbn "$ESP_FQBN" "$sketch_dir"; then
            ok "${label} firmware uploaded"
            return 0
        fi
        warn "Upload failed for ${label}"
        ask_yn "Retry ${label}?" y
        [[ "$REPLY" == "y" ]] || { skip_note "${label} flash failed"; return 1; }

        echo
        echo "  ${C_BOLD}${label}${C_RESET} — ${C_CYAN}UART0 download mode${C_RESET} on ${port}:"
        print_esp_download_steps_one
        echo "    ${C_DIM}(Pi on the SCCS Core; ROM bootloader is on the same UART as the host protocol)${C_RESET}"
        echo
        read -r -p "  Enter when ready (s = skip this module)… " ans || true
        case "${ans,,}" in
            s|skip|a|abort|q|quit)
                skip_note "${label} flash skipped"
                return 1
                ;;
        esac
        ESP_PORT=""
        wait_rc=0
        wait_for_esp_port "$label" "$port" || wait_rc=$?
        if [[ "$wait_rc" -eq 2 ]]; then
            skip_note "${label} flash skipped during download wait"
            return 1
        fi
        if [[ "$wait_rc" -ne 0 ]]; then
            ask_yn "Retry ${label}? (n skips this module)" y
            [[ "$REPLY" == "y" ]] && continue
            skip_note "${label} flash abandoned"
            return 1
        fi
        port="$ESP_PORT"
    done
}

# GETVCC handshake after the user has already tapped RESET.
verify_esp_live() {
    local label="$1" port="$2" verify_try ans
    for verify_try in 1 2 3; do
        if verify_esp_protocol "$port"; then
            ok "${label} answered GETVCC — lighting MCU is live"
            return 0
        fi
        if (( verify_try == 3 )); then
            warn "${label} did not answer GETVCC — upload is done; continuing."
            info "You can tap RESET again after the installer finishes, then restart ${SERVICE_NAME}.service."
            return 0
        fi
        echo
        echo "  No GETVCC from ${label}. Tap ${C_BOLD}RESET${C_RESET} on ${label} ${C_DIM}(BOOT released)${C_RESET}, then Enter."
        read -r -p "  Enter to re-check (s = continue anyway)… " ans || true
        case "${ans,,}" in
            s|skip) return 0 ;;
        esac
    done
}

# One download-mode prompt for both MCUs, both uploads, then one RESET/verify.
flash_both_esps() {
    local ans wait_rc port1=/dev/ttyAMA2 port2=/dev/ttyAMA3
    local esp1_port="" esp2_port="" uploaded1=0 uploaded2=0

    if ! esp_uarts_ready; then
        activate_host_uarts || true
    fi
    if ! esp_uarts_ready; then
        skip_note "ESP32 flash skipped — /dev/ttyAMA2 and /dev/ttyAMA3 are not active. Restart the Pi, then run: sudo $0 --esp"
        return 0
    fi

    while true; do
        echo
        echo "  Put ${C_BOLD}both ESP32s${C_RESET} into ${C_CYAN}UART0 download mode${C_RESET} together:"
        echo "    ${C_DIM}ESP32-1 (lighting / water) on ${port1} · ESP32-2 (lighting) on ${port2}${C_RESET}"
        print_esp_download_steps
        echo "    ${C_DIM}(Pi on the SCCS Core; ROM bootloader is on the same UART as the host protocol)${C_RESET}"
        echo
        echo "  ${C_YELLOW}!${C_RESET} Pi not on the SCCS Core? Type ${C_BOLD}s${C_RESET} to skip flashing."
        echo "  ${C_DIM}You can also type s during the serial wait if you started this by mistake.${C_RESET}"
        echo
        read -r -p "  Enter when both are in download mode (s = skip)… " ans || true
        case "${ans,,}" in
            s|skip|a|abort|q|quit)
                skip_note "ESP32 flash skipped"
                return 0
                ;;
        esac

        esp1_port=""
        esp2_port=""
        wait_rc=0
        wait_for_esp_port "ESP32-1 (lighting / water)" "$port1" || wait_rc=$?
        if [[ "$wait_rc" -eq 2 ]]; then
            skip_note "ESP32 flash skipped during download wait"
            return 0
        fi
        [[ "$wait_rc" -eq 0 ]] && esp1_port="$ESP_PORT"

        wait_rc=0
        wait_for_esp_port "ESP32-2 (lighting)" "$port2" || wait_rc=$?
        if [[ "$wait_rc" -eq 2 ]]; then
            skip_note "ESP32 flash skipped during download wait"
            return 0
        fi
        [[ "$wait_rc" -eq 0 ]] && esp2_port="$ESP_PORT"

        if [[ -z "$esp1_port" && -z "$esp2_port" ]]; then
            ask_yn "No serial ports yet. Retry download mode?" y
            [[ "$REPLY" == "y" ]] && continue
            skip_note "ESP32 flash abandoned"
            return 0
        fi
        if [[ -z "$esp1_port" ]]; then
            warn "ESP32-1 port not found — flashing ESP32-2 only"
        fi
        if [[ -z "$esp2_port" ]]; then
            warn "ESP32-2 port not found — flashing ESP32-1 only"
        fi
        break
    done

    uploaded1=0
    uploaded2=0
    if [[ -n "$esp1_port" ]]; then
        if upload_esp_firmware "esp32/esp32_1" "ESP32-1 (lighting / water)" "$esp1_port"; then
            uploaded1=1
        fi
    fi
    if [[ -n "$esp2_port" ]]; then
        if upload_esp_firmware "esp32/esp32_2" "ESP32-2 (lighting)" "$esp2_port"; then
            uploaded2=1
        fi
    fi

    if [[ "$uploaded1" -eq 0 && "$uploaded2" -eq 0 ]]; then
        return 0
    fi

    echo
    if [[ "$uploaded1" -eq 1 && "$uploaded2" -eq 1 ]]; then
        echo "  Tap ${C_BOLD}RESET${C_RESET} on ${C_BOLD}both ESP32s${C_RESET} ${C_DIM}(do not hold BOOT)${C_RESET} to run the new firmware."
    elif [[ "$uploaded1" -eq 1 ]]; then
        echo "  Tap ${C_BOLD}RESET${C_RESET} on ${C_BOLD}ESP32-1${C_RESET} ${C_DIM}(do not hold BOOT)${C_RESET} to run the new firmware."
    else
        echo "  Tap ${C_BOLD}RESET${C_RESET} on ${C_BOLD}ESP32-2${C_RESET} ${C_DIM}(do not hold BOOT)${C_RESET} to run the new firmware."
    fi
    echo "  ${C_DIM}The Pi UART has no RTS reset line, so esptool cannot start the app for you.${C_RESET}"
    echo
    read -r -p "  Enter after RESET (s = skip protocol check)… " ans || true
    case "${ans,,}" in
        s|skip) ;;
        *)
            [[ "$uploaded1" -eq 1 ]] && verify_esp_live "ESP32-1 (lighting / water)" "$esp1_port"
            [[ "$uploaded2" -eq 1 ]] && verify_esp_live "ESP32-2 (lighting)" "$esp2_port"
            ;;
    esac
    return 0
}

# mode: optional | required
step_esp() {
    local mode="${1:-optional}"
    step_begin "ESP32 firmware"
    require_checkout

    # However this step ends (skip, abort, compile fail, Ctrl-C), sccs must be up.
    trap 'trap - INT TERM ERR RETURN; ensure_sccs_service_running' RETURN
    trap 'ensure_sccs_service_running; exit 130' INT
    trap 'ensure_sccs_service_running; exit 143' TERM
    trap 'ensure_sccs_service_running' ERR

    if [[ "$mode" == "optional" ]]; then
        echo
        if ! sccs_core_detected; then
            info "If this Pi is ${C_BOLD}not${C_RESET} connected to the SCCS Core, skip this step."
        fi
        ask_sccs_core_flash "Is this Pi connected to the SCCS Core (flash both ESP32s now)?" y
        if [[ "$REPLY" != "y" ]]; then
            skip_note "ESP32 flash skipped — re-run when the Pi is on the SCCS Core"
            return 0
        fi
    fi

    # The host UARTs have to exist before esptool can see a chip. Write the
    # overlays (in case this is --esp on a Pi that has not run the full
    # install) and load them into this boot. Do this before asking for
    # download mode — holding BOOT does not create /dev/ttyAMA*.
    if ! esp_uarts_ready; then
        configure_uart_overlays || true
        activate_host_uarts || true
    fi
    if ! esp_uarts_ready; then
        if [[ "${UART_REBOOT_DECLINED:-0}" -eq 1 || -n "${SCCS_RESUME_PHASE:-}" ]]; then
            skip_note "ESP32 flash skipped — restart the Pi so /dev/ttyAMA2 and /dev/ttyAMA3 exist, then run: sudo $0 --esp"
            if [[ "${SCCS_RESUME_PHASE:-}" == "esp" ]]; then
                clear_install_resume
            fi
            return 0
        fi
        if ! offer_restart_for_uarts esp; then
            skip_note "ESP32 flash skipped — restart the Pi so /dev/ttyAMA2 and /dev/ttyAMA3 exist, then run: sudo $0 --esp"
            return 0
        fi
    fi

    echo
    info "Firmware is loaded over ${C_BOLD}serial${C_RESET} when the Pi is fitted to the SCCS Core"
    info "Put ${C_BOLD}both ESP32s${C_RESET} in download mode together — this step flashes both in one go."
    info "If you start by mistake: type ${C_BOLD}s${C_RESET} to skip."
    echo

    ensure_arduino_cli
    info "Arduino ESP32 core (may take several minutes on first run)…"
    run_as_user env PATH="$PATH" arduino-cli core update-index
    run_as_user env PATH="$PATH" arduino-cli core install esp32:esp32
    ok "esp32:esp32 core ready"

    if systemctl is-active --quiet "${SERVICE_NAME}.service" 2>/dev/null; then
        info "Stopping ${SERVICE_NAME}.service during upload…"
        systemctl stop "${SERVICE_NAME}.service" || true
    fi

    info "Compiling sketches…"
    run_as_user env PATH="$PATH" arduino-cli compile --fqbn "$ESP_FQBN" "$SCCS_HOME/esp32/esp32_1"
    ok "Compiled esp32_1"
    run_as_user env PATH="$PATH" arduino-cli compile --fqbn "$ESP_FQBN" "$SCCS_HOME/esp32/esp32_2"
    ok "Compiled esp32_2"

    flash_both_esps
    ok "ESP32 flash step finished"
    if [[ "${SCCS_RESUME_PHASE:-}" == "esp" ]]; then
        clear_install_resume
    fi
}

# mode: optional | required
# Listen for one Victron Instant Readout device.
# Stdout: a status word, then an optional detail line.
#   OK | MISMATCH | NOT_HEARD | HEARD | BLE | NO_VENV
victron_probe() {
    local mode="probe" addr="" key=""
    if [[ "${1:-}" == "--discover" ]]; then
        mode="discover"
    else
        addr="$1"
        key="$2"
    fi
    local py="$SCCS_HOME/venv/bin/python" out=""
    if [[ ! -x "$py" ]]; then
        printf 'NO_VENV\n'
        return 0
    fi
    local -a cmd run_env
    if command -v timeout >/dev/null 2>&1; then
        cmd=(timeout 45 "$py" -)
    else
        cmd=("$py" -)
    fi
    run_env=("VICTRON_MODE=${mode}")
    if [[ "$mode" == "probe" ]]; then
        run_env+=("VICTRON_ADDR=${addr}" "VICTRON_KEY=${key}")
    fi
    # victron-probe-begin
    if ! out="$(env "${run_env[@]}" "${cmd[@]}" <<'PY'
import asyncio
import os
import sys

TIMEOUT = 20.0


def classify_victron_advert(data: bytes):
    """Product type from the clear Instant Readout header. The key is not in it."""
    if not data or not bytes(data).startswith(b"\x10") or len(data) < 5:
        return None
    import struct

    from victron_ble.devices import detect_device_type
    from victron_ble.devices.base import MODEL_ID_MAPPING

    raw = bytes(data)
    model_id = struct.unpack("<H", raw[2:4])[0]
    klass = detect_device_type(raw)
    if klass is None:
        role = "other"
        fallback = "Victron"
    elif klass.__name__ == "BatteryMonitor":
        role = "shunt"
        fallback = "Battery monitor"
    elif klass.__name__ == "SolarCharger":
        role = "mppt"
        fallback = "Solar charger"
    else:
        role = "other"
        fallback = klass.__name__
    model = MODEL_ID_MAPPING.get(model_id) or fallback
    return {"role": role, "model": str(model)}


def summary_line(parsed, device, adv) -> str:
    parts = []
    model = None
    if hasattr(parsed, "get_model_name"):
        try:
            model = parsed.get_model_name()
        except Exception:
            model = None
    if model and not str(model).startswith("<Unknown"):
        parts.append(str(model))
    elif getattr(device, "name", None):
        parts.append(str(device.name))

    def add(getter, fmt):
        if not hasattr(parsed, getter):
            return
        try:
            val = getattr(parsed, getter)()
        except Exception:
            return
        if val is None:
            return
        parts.append(fmt(val))

    add("get_voltage", lambda v: f"{float(v):.2f} V")
    add("get_battery_voltage", lambda v: f"{float(v):.2f} V")
    add("get_soc", lambda v: f"{float(v):.0f}%")
    add("get_current", lambda v: f"{float(v):.2f} A")
    add("get_battery_charging_current", lambda v: f"{float(v):.2f} A")
    add("get_solar_power", lambda v: f"{float(v):.0f} W")
    add(
        "get_charge_state",
        lambda v: str(getattr(v, "name", v)).replace("_", " "),
    )
    rssi = getattr(adv, "rssi", None)
    if rssi is not None:
        parts.append(f"RSSI {int(rssi)}")
    return " · ".join(parts) if parts else "Instant Readout decrypted"


def handle(state, address, key, device, adv) -> None:
    if state.get("done"):
        return
    if (getattr(device, "address", "") or "").lower() != address:
        return
    state["heard"] = True
    mfr = getattr(adv, "manufacturer_data", None) or {}
    data = mfr.get(0x02E1)
    if not data or not bytes(data).startswith(b"\x10"):
        return
    from victron_ble.devices import detect_device_type
    from victron_ble.exceptions import AdvertisementKeyMismatchError

    klass = detect_device_type(bytes(data))
    if klass is None:
        state["status"] = "HEARD"
        state["detail"] = (
            "The address is advertising, but the packet is not a known Instant Readout."
        )
        return
    try:
        parsed = klass(key).parse(bytes(data))
    except AdvertisementKeyMismatchError:
        state["status"] = "MISMATCH"
        state["done"] = True
        return
    except Exception as exc:
        state["status"] = "BLE"
        state["detail"] = str(exc).strip() or exc.__class__.__name__
        return
    state["status"] = "OK"
    state["detail"] = summary_line(parsed, device, adv)
    state["done"] = True


def finish(state) -> None:
    if not state["done"] and state["heard"] and state["status"] == "NOT_HEARD":
        state["status"] = "HEARD"
        state["detail"] = (
            "The Pi can see that Bluetooth address, but it is not sending Instant Readout."
        )


async def discover_main() -> None:
    from bleak import BleakScanner

    found: dict = {}

    def callback(device, adv) -> None:
        mfr = getattr(adv, "manufacturer_data", None) or {}
        data = mfr.get(0x02E1)
        if not data:
            return
        info = classify_victron_advert(bytes(data))
        if not info:
            return
        addr = (getattr(device, "address", "") or "").strip().lower()
        if not addr or addr in found:
            return
        name = (getattr(device, "name", None) or "").replace("\t", " ").strip()
        rssi = getattr(adv, "rssi", None)
        found[addr] = (info, rssi, name)

    try:
        scanner = BleakScanner(detection_callback=callback)
        await scanner.start()
    except Exception as exc:
        print("BLE")
        print(str(exc).strip() or exc.__class__.__name__)
        return
    try:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + TIMEOUT
        while loop.time() < deadline:
            await asyncio.sleep(0.25)
    finally:
        try:
            await scanner.stop()
        except Exception:
            pass

    def sort_key(item):
        _addr, (_info, rssi, _name) = item
        return -(rssi if isinstance(rssi, (int, float)) else -999)

    for addr, (info, rssi, name) in sorted(found.items(), key=sort_key):
        rssi_s = "" if rssi is None else str(int(rssi))
        print("\t".join((info["role"], info["model"], addr, rssi_s, name)))


async def main() -> None:
    if os.environ.get("VICTRON_MODE") == "discover":
        await discover_main()
        return

    from bleak import BleakScanner

    address = os.environ["VICTRON_ADDR"].strip().lower()
    key = os.environ["VICTRON_KEY"].strip().lower()
    state = {"done": False, "status": "NOT_HEARD", "detail": "", "heard": False}

    def callback(device, adv):
        handle(state, address, key, device, adv)

    try:
        scanner = BleakScanner(detection_callback=callback)
        await scanner.start()
    except Exception as exc:
        print("BLE")
        print(str(exc).strip() or exc.__class__.__name__)
        return
    try:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + TIMEOUT
        next_note = loop.time() + 5
        while loop.time() < deadline and not state["done"]:
            if loop.time() >= next_note:
                print("  · still listening…", file=sys.stderr, flush=True)
                next_note += 5
            await asyncio.sleep(0.25)
    finally:
        try:
            await scanner.stop()
        except Exception:
            pass
    finish(state)
    print(state["status"])
    if state["detail"]:
        print(state["detail"])


asyncio.run(main())
PY
    )"; then
        printf 'BLE\nprobe failed\n'
        return 0
    fi
    # victron-probe-end
    printf '%s\n' "$out"
}

victron_discover() {
    victron_probe --discover
}

step_victron() {
    local mode="${1:-optional}"
    step_begin "Victron Equipment"
    require_conf

    echo
    info "In the ${C_BOLD}VictronConnect${C_RESET} app you should already have:"
    info "  · SmartShunt (or BMV) and MPPT added, firmware current"
    info "  · Devices ${C_BOLD}meshed${C_RESET} if you use VE.Smart Networking"
    info "  · ${C_BOLD}Instant readout via Bluetooth${C_RESET} enabled on each device"
    info "  · gear → Product info → Instant readout → Show  (32-character key)"
    echo
    info "The Pi listens for the devices and fills in the Bluetooth addresses."
    info "The key is not in the broadcast. It is not the sticker PIN."
    info "Each device is offered the enrolled Instant Readout keys. Press Enter to take that device's key, type 1 or 2 to pick the other, or paste a new 32-character key."
    info "Each device is checked live. A mismatch or a missed advertisement can be retyped."
    echo

    local cur_sa cur_sk cur_ma cur_mk
    cur_sa="$(conf_get victron shunt_address)"
    cur_sk="$(conf_get victron shunt_key)"
    cur_ma="$(conf_get victron mppt_address)"
    cur_mk="$(conf_get victron mppt_key)"
    if [[ -n "$cur_sa$cur_ma$cur_sk$cur_mk" ]]; then
        info "Current:"
        info "  shunt MAC=${cur_sa:-∅}  key=${cur_sk:-empty}"
        info "  mppt  MAC=${cur_ma:-∅}  key=${cur_mk:-empty}"
    fi

    if [[ "$mode" == "optional" ]]; then
        ask_yn "Configure Victron SmartShunt / MPPT now?" y
        if [[ "$REPLY" != "y" ]]; then
            skip_note "Victron skipped — use menu item when you have MAC+keys"
            return 0
        fi
    fi

    info "Ensuring Bluetooth is available…"
    systemctl enable --now bluetooth >/dev/null 2>&1 || true
    rfkill unblock bluetooth 2>/dev/null || true
    command -v hciconfig >/dev/null 2>&1 && hciconfig hci0 up 2>/dev/null || true
    if [[ -n "${CONFIG_TXT:-}" ]] && grep -qE '^\s*dtoverlay=disable-bt' "$CONFIG_TXT" 2>/dev/null; then
        warn "dtoverlay=disable-bt is set — remove it and reboot for Instant Readout"
        NEED_REBOOT=1
    fi
    ok "Bluetooth service enabled"

    prompt_mac() {
        local label="$1" current="$2" out
        while true; do
            read -r -p "  ${label} MAC [${current:-empty / s to skip}]: " ans || true
            ans="${ans:-$current}"
            if [[ -z "$ans" || "${ans,,}" == "s" || "${ans,,}" == "skip" ]]; then
                PROMPT_VAL=""; return 1
            fi
            if out="$(normalize_mac "$ans")"; then
                PROMPT_VAL="$out"; return 0
            fi
            warn "Invalid MAC — use aa:bb:cc:dd:ee:ff"
        done
    }
    # Enrolled Instant Readout keys. Suggested for every install, including a
    # fresh sccs.conf that has not had Victron filled in yet.
    local VICTRON_SUGGEST_SHUNT_KEY="9e120a6e853e45cc0060d32d5a305f9c"
    local VICTRON_SUGGEST_MPPT_KEY="80534dc5067a80d39d25f4140908da4e"
    prompt_key() {
        local label="$1" current="$2" out shown="" suggest="" default_ans=""
        if [[ -n "$current" ]]; then
            shown="$(normalize_victron_key "$current" 2>/dev/null || true)"
        fi
        if [[ "$label" == "MPPT" ]]; then
            suggest="$VICTRON_SUGGEST_MPPT_KEY"
        else
            suggest="$VICTRON_SUGGEST_SHUNT_KEY"
        fi
        # A key already saved for this device stays the Enter default.
        # Otherwise Enter takes the enrolled key for this device.
        default_ans="${shown:-$suggest}"
        echo
        info "  Suggested Instant Readout keys:"
        info "    1) SmartShunt  ${VICTRON_SUGGEST_SHUNT_KEY}"
        info "    2) MPPT        ${VICTRON_SUGGEST_MPPT_KEY}"
        while true; do
            read -r -p "  ${label} key — Enter uses ${default_ans} (1, 2, paste, or s to skip): " ans || true
            if [[ -z "$ans" ]]; then
                ans="$default_ans"
            fi
            case "${ans,,}" in
                1|shunt) ans="$VICTRON_SUGGEST_SHUNT_KEY" ;;
                2|mppt) ans="$VICTRON_SUGGEST_MPPT_KEY" ;;
                s|skip)
                    PROMPT_VAL=""
                    return 1
                    ;;
            esac
            if out="$(normalize_victron_key "$ans")"; then
                PROMPT_VAL="$out"
                return 0
            fi
            warn "Type 1, 2, or exactly 32 hex characters"
        done
    }

    pick_victron_mac() {
        local role="$1" label="$2" current="$3"
        local -a macs=() models=() rssis=()
        local line role_f model mac rssi name i choice
        for line in "${VICTRON_SCAN_LINES[@]}"; do
            IFS=$'\t' read -r role_f model mac rssi name <<<"$line"
            [[ "$role_f" == "$role" ]] || continue
            macs+=("$mac")
            models+=("$model")
            rssis+=("$rssi")
        done
        if [[ ${#macs[@]} -eq 1 ]]; then
            info "Using ${models[0]} at ${macs[0]}"
            PROMPT_VAL="${macs[0]}"
            return 0
        fi
        if [[ ${#macs[@]} -gt 1 ]]; then
            info "Choose the ${label}:"
            for i in "${!macs[@]}"; do
                info "  $((i + 1))) ${models[$i]}  ${macs[$i]}${rssis[$i]:+  RSSI ${rssis[$i]}}"
            done
            info "  m) Type a MAC"
            info "  s) Skip"
            if ! read_input "  Choice [1]: "; then
                return 1
            fi
            choice="${REPLY_LINE:-1}"
            case "${choice,,}" in
                s|skip) return 1 ;;
                m)
                    prompt_mac "$label" "$current"
                    return $?
                    ;;
            esac
            if [[ "$choice" =~ ^[0-9]+$ ]] && (( choice >= 1 && choice <= ${#macs[@]} )); then
                PROMPT_VAL="${macs[$((choice - 1))]}"
                return 0
            fi
            warn "Not a listed choice."
            prompt_mac "$label" "$current"
            return $?
        fi
        warn "No ${label} heard. It needs to be on, nearby, with Instant Readout enabled."
        prompt_mac "$label" "$current"
    }

    # On success, append field/value pairs to the named array.
    # A failed check offers another entry and does not replace saved credentials.
    confirm_victron_device() {
        local label="$1" addr_field="$2" key_field="$3"
        local orig_addr="$4" orig_key="$5"
        local -n _saved="$6"
        local addr="$orig_addr" key="$orig_key"
        local out="" status="" detail="" manual=0
        local role="shunt"
        [[ "$label" == "MPPT" ]] && role="mppt"
        echo
        info "$label"
        while true; do
            if [[ "$manual" -eq 1 ]]; then
                manual=0
                if ! prompt_mac "$label" "$addr"; then
                    if [[ -n "$orig_addr" || -n "$orig_key" ]]; then
                        info "Left the saved ${label} credentials unchanged."
                    else
                        info "Skipped ${label}"
                    fi
                    return 0
                fi
            elif ! pick_victron_mac "$role" "$label" "$addr"; then
                if [[ -n "$orig_addr" || -n "$orig_key" ]]; then
                    info "Left the saved ${label} credentials unchanged."
                else
                    info "Skipped ${label}"
                fi
                return 0
            fi
            addr="$PROMPT_VAL"
            if ! prompt_key "$label" "$key"; then
                warn "${label} needs the 32-character Instant Readout key before it can connect."
                ask_yn "Retype the MAC and key?" y
                if [[ "$REPLY" == "y" ]]; then
                    continue
                fi
                if [[ -n "$orig_addr" || -n "$orig_key" ]]; then
                    info "Left the saved ${label} credentials unchanged."
                else
                    info "Skipped ${label}"
                fi
                return 0
            fi
            key="$PROMPT_VAL"
            info "Listening for ${label} at ${addr}…"
            out="$(victron_probe "$addr" "$key")"
            status="${out%%$'\n'*}"
            if [[ "$out" == *$'\n'* ]]; then
                detail="${out#*$'\n'}"
            else
                detail=""
            fi
            # BlueZ refuses a second discovery while sccs.service is scanning.
            if [[ "$status" == "BLE" ]] && [[ "${detail,,}" == *inprogress* || "${detail,,}" == *"already in progress"* ]]; then
                if systemctl is-active --quiet "${SERVICE_NAME}.service" 2>/dev/null; then
                    info "Stopping ${SERVICE_NAME}.service so the Bluetooth scan can start…"
                    systemctl stop "${SERVICE_NAME}.service" || true
                    VICTRON_SERVICE_STOPPED=1
                    out="$(victron_probe "$addr" "$key")"
                    status="${out%%$'\n'*}"
                    if [[ "$out" == *$'\n'* ]]; then
                        detail="${out#*$'\n'}"
                    else
                        detail=""
                    fi
                fi
            fi
            case "$status" in
                OK)
                    ok "${label} connected — ${detail:-Instant Readout decrypted}"
                    _saved+=("$addr_field" "$addr" "$key_field" "$key")
                    return 0
                    ;;
                MISMATCH)
                    warn "The Instant Readout key does not match ${label} (${addr})."
                    ;;
                NOT_HEARD)
                    warn "No Bluetooth advertisement from ${addr}."
                    info "The device needs to be on, nearby, with Instant Readout enabled."
                    ;;
                HEARD)
                    warn "${detail:-${addr} is visible, but it is not sending Instant Readout.}"
                    ;;
                NO_VENV)
                    warn "Cannot check the connection — the Python venv is not installed yet."
                    ask_yn "Save these credentials without a live check?" n
                    if [[ "$REPLY" == "y" ]]; then
                        _saved+=("$addr_field" "$addr" "$key_field" "$key")
                        return 0
                    fi
                    ;;
                *)
                    warn "Could not read ${label}${detail:+: ${detail}}"
                    ;;
            esac
            ask_yn "Retype the key?" y
            if [[ "$REPLY" != "y" ]]; then
                if [[ -n "$orig_addr" || -n "$orig_key" ]]; then
                    info "Left the saved ${label} credentials unchanged."
                else
                    info "Skipped ${label}"
                fi
                return 0
            fi
            ask_yn "Type a different MAC?" n
            [[ "$REPLY" == "y" ]] && manual=1
        done
    }

    local shunt_args=() mppt_args=() victron_args=()
    local service_was_active=0
    local scan_out="" scan_line="" role_f="" model="" mac="" rssi="" name="" role_label=""
    VICTRON_SERVICE_STOPPED=0
    VICTRON_SCAN_LINES=()
    if systemctl is-active --quiet "${SERVICE_NAME}.service" 2>/dev/null; then
        service_was_active=1
        info "Stopping ${SERVICE_NAME}.service so the Bluetooth scan can start…"
        systemctl stop "${SERVICE_NAME}.service" || true
        VICTRON_SERVICE_STOPPED=1
    fi

    info "Listening for Victron Instant Readout (about 20 seconds)…"
    scan_out="$(victron_discover)"
    if [[ "${scan_out%%$'\n'*}" == "BLE" || "${scan_out%%$'\n'*}" == "NO_VENV" ]]; then
        warn "Could not scan for Victron devices."
        [[ "$scan_out" == *$'\n'* ]] && warn "${scan_out#*$'\n'}"
    else
        while IFS= read -r scan_line; do
            [[ -n "$scan_line" ]] || continue
            VICTRON_SCAN_LINES+=("$scan_line")
        done <<<"$scan_out"
    fi
    if [[ ${#VICTRON_SCAN_LINES[@]} -eq 0 ]]; then
        warn "No Instant Readout devices heard. You can still type a MAC."
    else
        info "Heard:"
        for scan_line in "${VICTRON_SCAN_LINES[@]}"; do
            IFS=$'\t' read -r role_f model mac rssi name <<<"$scan_line"
            role_label="$role_f"
            [[ "$role_f" == "shunt" ]] && role_label="Shunt"
            [[ "$role_f" == "mppt" ]] && role_label="MPPT"
            info "  ${model} (${role_label}) ${mac}${rssi:+  RSSI ${rssi}}${name:+  ${name}}"
        done
    fi

    confirm_victron_device "Shunt" shunt_address shunt_key "$cur_sa" "$cur_sk" shunt_args
    confirm_victron_device "MPPT" mppt_address mppt_key "$cur_ma" "$cur_mk" mppt_args

    [[ ${#shunt_args[@]} -gt 0 ]] && victron_args+=("${shunt_args[@]}")
    [[ ${#mppt_args[@]} -gt 0 ]] && victron_args+=("${mppt_args[@]}")
    if [[ ${#victron_args[@]} -gt 0 ]]; then
        conf_set victron "${victron_args[@]}"
        ok "Updated [victron]"
    else
        info "No Victron credentials saved"
    fi
    if [[ "$VICTRON_SERVICE_STOPPED" -eq 1 || ( ${#victron_args[@]} -gt 0 && "$service_was_active" -eq 1 ) ]]; then
        restart_sccs_service "to load Victron credentials"
    fi
}

ensure_voice_sections() {
    require_conf
    python3 - "$CONF" <<'PY'
import re, sys
path = sys.argv[1]
text = open(path, encoding="utf-8").read()
changed = False

def has_section(name):
    return re.search(rf"(?m)^\[{re.escape(name)}\][ \t]*\r?\n", text) is not None

blocks = {
    "homekit": """
[homekit]
enabled = false
name = SCCS
pin =
port = 51826
bind_address = 10.10.10.1
persist_file = config/homekit.state
low_battery_percent = 20
""",
    "matter": """
[matter]
enabled = false
name = SCCS
port = 5540
bind_address = 10.10.10.1
persist_file = config/matter.state
""",
}
for name, block in blocks.items():
    if not has_section(name):
        if not text.endswith("\n"):
            text += "\n"
        text += block
        if not text.endswith("\n"):
            text += "\n"
        changed = True
if changed:
    open(path, "w", encoding="utf-8").write(text)
    print("added")
else:
    print("ok")
PY
    chown "$USERNAME":www-data "$CONF" 2>/dev/null || true
}

# Avahi is shared by HomeKit and Google Home. One install per step.
VOICE_AVAHI_READY=0
ensure_voice_avahi() {
    if [[ "${VOICE_AVAHI_READY:-0}" == "1" ]]; then
        return 0
    fi
    if ! command -v avahi-daemon >/dev/null 2>&1; then
        info "Installing Avahi for phone discovery on the van LAN…"
        DEBIAN_FRONTEND=noninteractive apt-get install -y \
            avahi-daemon libavahi-compat-libdnssd1 avahi-utils
    fi
    systemctl enable --now avahi-daemon >/dev/null 2>&1 || true
    if systemctl is-active --quiet avahi-daemon; then
        ok "Avahi is running"
    else
        warn "Avahi did not start — phone discovery needs it"
    fi
    VOICE_AVAHI_READY=1
}

install_homekit_packages() {
    info "Installing Apple HomeKit / Siri…"
    ensure_voice_avahi
    if [[ -x "$SCCS_HOME/venv/bin/pip" ]]; then
        run_as_user "$SCCS_HOME/venv/bin/pip" install --upgrade 'HAP-python[QRCode]'
        ok "Apple HomeKit / Siri installed"
    else
        warn "Python venv missing — run Install SCCS, then menu 10"
    fi
}

install_google_packages() {
    if [[ "${1:-}" == "refresh" ]]; then
        info "Refreshing Google Home / Gemini…"
    else
        info "Installing Google Home / Gemini…"
    fi
    ensure_voice_avahi
    info "Google Home needs Node.js and IPv6 on the van LAN."
    if ! command -v node >/dev/null 2>&1; then
        DEBIAN_FRONTEND=noninteractive apt-get install -y nodejs
    fi
    if ! command -v npm >/dev/null 2>&1; then
        DEBIAN_FRONTEND=noninteractive apt-get install -y npm || true
    fi
    if [[ -f "$SCCS_HOME/matter-bridge/package.json" ]] && command -v npm >/dev/null 2>&1; then
        run_as_user bash -c "cd \"$SCCS_HOME/matter-bridge\" && npm install --omit=dev"
        ok "Google Home / Gemini installed"
    elif ! command -v npm >/dev/null 2>&1; then
        warn "npm is not installed — Google Home will not start until: apt install npm && cd $SCCS_HOME/matter-bridge && npm install"
    fi
    if ! ip -6 -o addr show scope link 2>/dev/null | grep -q .; then
        warn "No IPv6 link-local address on this Pi — re-run menu 6 (Networking) so the LAN gets IPv6"
    else
        ok "IPv6 link-local is present"
    fi
}

# mode: optional | required
# $2: homekit | google | both | neither, when the caller already asked.
step_voice_assistants() {
    local mode="${1:-optional}"
    local choice="${2:-}"
    step_begin "HomeKit or Google Home"
    require_conf
    ensure_voice_sections
    VOICE_AVAHI_READY=0

    local cur_hk cur_gh
    cur_hk="$(conf_get homekit enabled)"
    cur_gh="$(conf_get matter enabled)"

    if [[ -z "$choice" ]]; then
        echo
        info "Phone and voice control. Pair from the Settings tab. The phone must be on van Wi‑Fi."
        info "Current: HomeKit=${cur_hk:-false}  Google Home=${cur_gh:-false}"
        if [[ "$mode" == "optional" ]]; then
            ask_yn "Install Apple HomeKit / Siri or Google Home / Gemini?" y
            if [[ "$REPLY" != "y" ]]; then
                skip_note "HomeKit or Google Home skipped — use menu 10 later"
                return 0
            fi
            ask_voice_product "$(voice_product_default)"
            choice="$REPLY"
        else
            ask_voice_product "$(voice_product_default)" y
            choice="$REPLY"
        fi
    else
        echo
        info "Current: HomeKit=${cur_hk:-false}  Google Home=${cur_gh:-false}"
        case "$choice" in
            homekit) info "Selected: Apple HomeKit / Siri" ;;
            google) info "Selected: Google Home / Gemini" ;;
            both) info "Selected: Apple HomeKit / Siri and Google Home / Gemini" ;;
            neither) info "Selected: neither" ;;
        esac
    fi

    local want_hk=n want_gh=n
    case "$choice" in
        homekit) want_hk=y ;;
        google) want_gh=y ;;
        both) want_hk=y; want_gh=y ;;
        neither) ;;
        *) die "Unknown voice choice: $choice" ;;
    esac

    local bind=""
    if [[ "$want_hk" == "y" || "$want_gh" == "y" ]]; then
        bind="$(conf_get homekit bind_address)"
        bind="${bind:-$(conf_get matter bind_address)}"
        bind="${bind:-${LAN_ADDR:-10.10.10.1}}"
        echo
        if ! read_input "  LAN address to advertise on [${bind}]: "; then
            die "No interactive terminal available for prompts"
        fi
        [[ -n "${REPLY_LINE:-}" ]] && bind="$REPLY_LINE"
    fi

    if [[ "$want_hk" == "y" ]]; then
        install_homekit_packages
    fi
    if [[ "$want_gh" == "y" ]]; then
        install_google_packages
    fi

    if [[ "$want_hk" == "y" || "$want_gh" == "y" ]]; then
        conf_set homekit enabled "$([ "$want_hk" == "y" ] && echo true || echo false)" bind_address "$bind"
        conf_set matter enabled "$([ "$want_gh" == "y" ] && echo true || echo false)" bind_address "$bind"
    else
        conf_set homekit enabled false
        conf_set matter enabled false
    fi
    if [[ "$want_hk" == "y" ]]; then
        ok "Apple HomeKit / Siri enabled"
    else
        ok "Apple HomeKit / Siri off"
    fi
    if [[ "$want_gh" == "y" ]]; then
        ok "Google Home / Gemini enabled"
    else
        ok "Google Home / Gemini off"
    fi

    local load_what="voice control"
    if [[ "$want_hk" == "y" && "$want_gh" == "y" ]]; then
        load_what="HomeKit and Google Home"
    elif [[ "$want_hk" == "y" ]]; then
        load_what="HomeKit"
    elif [[ "$want_gh" == "y" ]]; then
        load_what="Google Home"
    fi
    restart_sccs_service_if_active "to load ${load_what}"
    if [[ "$want_hk" == "y" || "$want_gh" == "y" ]]; then
        info "Open Settings on the UI to pair."
    fi
}

# iPhone / Android USB tethering. A USB Ethernet dongle (r8152, asix, ax88179)
# is not in this list and can still be the van LAN.
is_phone_uplink_if() {
    local dev="$1" driver=""
    [[ -n "$dev" ]] || return 1
    driver="$(basename "$(readlink -f "/sys/class/net/${dev}/device/driver" 2>/dev/null)" 2>/dev/null || true)"
    case "$driver" in
        ipheth|rndis_host|cdc_ncm|cdc_mbim|cdc_subset|qmi_wwan|huawei_cdc_ncm)
            return 0
            ;;
    esac
    return 1
}

# Bind the van LAN address to one NIC and keep it there across reboot.
#
# Raspberry Pi OS writes NetworkManager profiles back through netplan. Changing
# the generated "netplan-eth0" profile drops connection.interface-name, so the
# profile matches every ethernet device. The iPhone (ipheth, usually eth1) gets
# link before the onboard port and takes 10.10.10.1. The touchscreen port then
# has no address, and Pi-hole will not hand out a lease on it.
# A dedicated keyfile pinned to the NIC name and permanent MAC stays bound
# (same storage as the USB tether profile).
pin_lan_nm_connection() {
    local lan_if="$1" lan_addr="$2" cidr="$3"
    local mac="" con="sccs-lan" id="" ifn="" macset="" addrs=""

    mac="$(tr '[:upper:]' '[:lower:]' < "/sys/class/net/${lan_if}/address" 2>/dev/null || true)"

    if nmcli -t -f NAME connection show | grep -Fxq "$con"; then
        info "Updating NM connection ${con} for ${lan_if}"
        nmcli connection modify "$con" \
            connection.interface-name "$lan_if" \
            connection.autoconnect yes \
            connection.autoconnect-priority 100 \
            ipv4.method manual \
            ipv4.addresses "${lan_addr}/${cidr}" \
            ipv4.gateway "" \
            ipv4.dns "${lan_addr}" \
            ipv4.never-default yes \
            ipv6.method link-local \
            ipv6.never-default yes
    else
        info "Creating NM connection ${con} for ${lan_if}"
        nmcli connection add type ethernet ifname "$lan_if" con-name "$con" \
            connection.autoconnect yes \
            connection.autoconnect-priority 100 \
            ipv4.method manual \
            ipv4.addresses "${lan_addr}/${cidr}" \
            ipv4.gateway "" \
            ipv4.dns "${lan_addr}" \
            ipv4.never-default yes \
            ipv6.method link-local \
            ipv6.never-default yes
    fi
    if [[ -n "$mac" && "$mac" != "00:00:00:00:00:00" ]]; then
        nmcli connection modify "$con" 802-3-ethernet.mac-address "$mac"
    fi

    # Anything else that is unpinned, or that is tied to this port, will race
    # the phone at boot and can win. sccs-lan is the only profile for this NIC.
    while IFS= read -r id; do
        [[ -n "$id" && "$id" != "$con" ]] || continue
        [[ "$(nmcli -g connection.type connection show "$id" 2>/dev/null || true)" == "802-3-ethernet" ]] || continue
        ifn="$(nmcli -g connection.interface-name connection show "$id" 2>/dev/null || true)"
        macset="$(nmcli -g 802-3-ethernet.mac-address connection show "$id" 2>/dev/null || true)"
        addrs="$(nmcli -g ipv4.addresses connection show "$id" 2>/dev/null || true)"
        [[ "$ifn" == "--" ]] && ifn=""
        [[ "$macset" == "--" ]] && macset=""
        if [[ "$ifn" == "$lan_if" ]] || { [[ -z "$ifn" && -z "$macset" ]]; } || [[ "$addrs" == *"${lan_addr}/"* ]]; then
            info "Removing ethernet profile '${id}' so it cannot take ${lan_addr} off ${lan_if}"
            nmcli connection down "$id" 2>/dev/null || true
            nmcli connection delete "$id" 2>/dev/null \
                || nmcli connection modify "$id" connection.autoconnect no \
                || true
        fi
    done < <(nmcli -t -f NAME connection show)

    nmcli connection up "$con" || warn "Could not activate ${con} on ${lan_if}"
}

# ---------------------------------------------------------------------------
# Integrated: LAN IP + NAT + Pi-hole (DNS + DHCP)
# Env: LAN_IF WAN_IF EXTRA_WAN_IF WAN_IFS LAN_ADDR LAN_CIDR DHCP_* SCCS_CONF
# ---------------------------------------------------------------------------
apply_lan_nat_dhcp() {
    local LAN_IF="${LAN_IF:-eth0}"
    local WAN_IF="${WAN_IF:-wlan0}"
    local LAN_ADDR="${LAN_ADDR:-10.10.10.1}"
    local LAN_CIDR="${LAN_CIDR:-24}"
    local DHCP_RANGE_START="${DHCP_RANGE_START:-10.10.10.50}"
    local DHCP_RANGE_END="${DHCP_RANGE_END:-10.10.10.200}"
    local DHCP_LEASE="${DHCP_LEASE:-12h}"
    local SCCS_CONF="${SCCS_CONF:-$CONF}"
    local POLKIT_SRC="$SCCS_HOME/config/polkit/50-sccs-networkmanager.rules"
    local POLKIT_DEST="/etc/polkit-1/rules.d/50-sccs-networkmanager.rules"
    local SYSCTL_DROPIN="/etc/sysctl.d/99-sccs-forward.conf"
    local NFTABLES_CONF="/etc/nftables.conf"
    local PIHOLE_STATIC="/etc/dnsmasq.d/99-sccs-static-dhcp.conf"
    local PIHOLE_WAN_EXCEPT="/etc/dnsmasq.d/02-sccs-wan-except.conf"
    local PIHOLE_PASS_FILE="/root/.sccs-pihole-web-password"

    [[ -f "$SCCS_CONF" ]] || die "Missing config: $SCCS_CONF"
    ip link show "$LAN_IF" &>/dev/null || die "LAN interface not found: $LAN_IF"
    if is_phone_uplink_if "$LAN_IF"; then
        die "Refusing to use ${LAN_IF} as the van LAN — that interface is a phone USB tether"
    fi

    local WAN_LIST=() WAN_CLEAN=() w rest name ip mac
    if [[ -n "${WAN_IFS:-}" ]]; then
        # shellcheck disable=SC2206
        WAN_LIST=($WAN_IFS)
    else
        WAN_LIST=("$WAN_IF")
        [[ -n "${EXTRA_WAN_IF:-}" ]] && WAN_LIST+=("$EXTRA_WAN_IF")
    fi
    declare -A _seen_wan=()
    for w in "${WAN_LIST[@]}"; do
        [[ -z "$w" || "$w" == "$LAN_IF" ]] && continue
        [[ -n "${_seen_wan[$w]:-}" ]] && continue
        if ! ip link show "$w" &>/dev/null; then
            warn "WAN interface not found (skipped): $w"
            continue
        fi
        _seen_wan[$w]=1
        WAN_CLEAN+=("$w")
    done
    WAN_LIST=("${WAN_CLEAN[@]}")
    [[ ${#WAN_LIST[@]} -gt 0 ]] || die "No WAN interfaces available — need wlan0 and/or USB tether"

    [[ -f "$POLKIT_SRC" ]] || die "Missing polkit rule: $POLKIT_SRC"
    install -m 644 "$POLKIT_SRC" "$POLKIT_DEST"
    ok "Installed $POLKIT_DEST"

    mapfile -t RESERVATIONS < <(python3 - "$SCCS_CONF" "${LAN_ADDR:-10.10.10.1}" <<'PY'
import configparser, ipaddress, re, subprocess, sys
path, lan_addr = sys.argv[1], sys.argv[2]
cfg = configparser.ConfigParser()
cfg.read(path)
if not cfg.has_section("screens"):
    sys.exit(0)

blocked = set()
try:
    blocked.add(ipaddress.IPv4Address(lan_addr))
except Exception:
    pass
try:
    out = subprocess.check_output(
        ["ip", "-4", "-o", "addr", "show", "scope", "global"],
        text=True, timeout=3,
    )
    for line in out.splitlines():
        for tok in line.split():
            if "/" in tok:
                try:
                    blocked.add(ipaddress.IPv4Address(tok.split("/", 1)[0]))
                except Exception:
                    pass
except Exception:
    pass

mac_re = re.compile(r"^([0-9a-f]{2}:){5}[0-9a-f]{2}$")
seen_ips = {}
for name, line in cfg.items("screens"):
    parts = [p.strip() for p in str(line).split("|")]
    if len(parts) < 11:
        print(f"skip {name}: need host + mac", file=sys.stderr)
        continue
    host, mac = parts[2], parts[10].strip().lower()
    if not mac or mac in ("none", "-"):
        print(f"skip {name}: empty mac", file=sys.stderr)
        continue
    if not mac_re.match(mac):
        print(f"skip {name}: bad mac {mac!r}", file=sys.stderr)
        continue
    try:
        ip_obj = ipaddress.IPv4Address(host)
        ip = str(ip_obj)
    except Exception:
        print(f"skip {name}: host must be IPv4", file=sys.stderr)
        continue
    if ip_obj in blocked:
        print(f"skip {name}: host {ip} is this Pi — refusing DHCP reservation", file=sys.stderr)
        continue
    if ip in seen_ips:
        print(f"skip {name}: host {ip} already reserved for {seen_ips[ip]}", file=sys.stderr)
        continue
    seen_ips[ip] = name
    print(f"{name}\t{ip}\t{mac}")
PY
)
    if [[ ${#RESERVATIONS[@]} -eq 0 ]]; then
        info "No DHCP reservations yet — normal on first install before you edit [screens]."
        info "After setting host + mac under [screens], re-run menu 6 (or menu 8) to apply them."
    fi

    info "LAN $LAN_IF = ${LAN_ADDR}/${LAN_CIDR} · WAN: ${WAN_LIST[*]}"
    command -v nmcli >/dev/null 2>&1 || die "nmcli not found — install NetworkManager"

    pin_lan_nm_connection "$LAN_IF" "$LAN_ADDR" "$LAN_CIDR"
    ip -4 addr show dev "$LAN_IF" | sed -n 's/^/    /p'

    echo "net.ipv4.ip_forward=1" >"$SYSCTL_DROPIN"
    sysctl -w net.ipv4.ip_forward=1 >/dev/null
    ok "IP forwarding enabled"

    command -v nft >/dev/null 2>&1 || apt-get install -y nftables
    local FORWARD_RULES="" MASQ_RULES=""
    for w in "${WAN_LIST[@]}"; do
        FORWARD_RULES+="        iifname \"${LAN_IF}\" oifname \"${w}\" accept"$'\n'
        FORWARD_RULES+="        iifname \"${w}\" oifname \"${LAN_IF}\" ct state established,related accept"$'\n'
        MASQ_RULES+="        oifname \"${w}\" masquerade"$'\n'
    done
    cat >"$NFTABLES_CONF" <<EOF
#!/usr/sbin/nft -f
# Generated by SCCS install.sh — LAN gateway
# WAN: ${WAN_LIST[*]}

flush ruleset

table inet filter {
    chain input {
        type filter hook input priority filter; policy accept;
    }
    chain forward {
        type filter hook forward priority filter; policy drop;
${FORWARD_RULES}    }
    chain output {
        type filter hook output priority filter; policy accept;
    }
}

table ip nat {
    chain postrouting {
        type nat hook postrouting priority srcnat; policy accept;
${MASQ_RULES}    }
}
EOF
    chmod 644 "$NFTABLES_CONF"
    nft -f "$NFTABLES_CONF"
    systemctl enable --now nftables >/dev/null 2>&1 || true
    ok "nftables NAT: ${LAN_IF} → ${WAN_LIST[*]}"

    # System dnsmasq must not own DHCP/DNS — Pi-hole FTL does
    info "Stopping system dnsmasq (DHCP/DNS handled by Pi-hole)…"
    systemctl disable --now dnsmasq 2>/dev/null || true
    systemctl mask dnsmasq 2>/dev/null || true
    rm -f /etc/dnsmasq.d/sccs-lan.conf /etc/dnsmasq.d/50-sccs-screens.conf

    # --- Pi-hole (DNS + DHCP) ---
    # First time this server hands out addresses. Panels already powered on
    # keep their old address until someone restarts them.
    local dhcp_newly_active=0 dhcp_state=""
    if ! command -v pihole >/dev/null 2>&1; then
        dhcp_newly_active=1
    elif command -v pihole-FTL >/dev/null 2>&1; then
        dhcp_state="$(pihole-FTL --config dhcp.active 2>/dev/null || true)"
        dhcp_state="${dhcp_state,,}"
        dhcp_state="${dhcp_state//[$' \t\r\n\"']/}"
        if [[ -n "$dhcp_state" && "$dhcp_state" != "true" ]]; then
            dhcp_newly_active=1
        fi
    fi
    # A USB Ethernet dongle may enumerate as eth1 and already hold LAN_ADDR.
    # Do not follow the address onto a phone tether: that is how eth0 lost
    # 10.10.10.1 and the touchscreen stopped getting a lease.
    local lan_if_resolved=""
    lan_if_resolved="$(ip -4 -o addr show dev "$LAN_IF" 2>/dev/null \
        | awk -v a="${LAN_ADDR}/" '$0 ~ a {print $2; exit}')"
    if [[ -z "$lan_if_resolved" ]]; then
        lan_if_resolved="$(ip -4 -o addr show 2>/dev/null \
            | awk -v a="${LAN_ADDR}/" '$0 ~ a {print $2; exit}')"
        if [[ -n "$lan_if_resolved" && "$lan_if_resolved" != "$LAN_IF" ]]; then
            if is_phone_uplink_if "$lan_if_resolved"; then
                warn "LAN address ${LAN_ADDR} is on phone uplink ${lan_if_resolved} — Pi-hole stays on ${LAN_IF}"
            else
                warn "LAN address ${LAN_ADDR} is on ${lan_if_resolved}, not ${LAN_IF} — using ${lan_if_resolved} for Pi-hole"
                LAN_IF="$lan_if_resolved"
            fi
        fi
    fi

    if ! command -v pihole >/dev/null 2>&1; then
        info "Installing Pi-hole (unattended) — this can take several minutes…"
        mkdir -p /etc/pihole
        # Pre-seed classic setupVars (basic-install still uses these for --unattended).
        # Upstream must be bare IPs/hostnames — "1.1.1.1#cloudflare" is NOT valid
        # dnsmasq (the #suffix is treated as a port and FTL aborts with "bad port").
        cat >/etc/pihole/setupVars.conf <<EOF
PIHOLE_INTERFACE=${LAN_IF}
QUERY_LOGGING=true
INSTALL_WEB_SERVER=true
INSTALL_WEB_INTERFACE=true
LIGHTTPD_ENABLED=false
CACHE_SIZE=10000
DNS_FQDN_REQUIRED=true
DNS_BOGUS_PRIV=true
DNSMASQ_LISTENING=local
WEBPASSWORD=
BLOCKING_ENABLED=true
PIHOLE_DNS_1=1.1.1.1
PIHOLE_DNS_2=1.0.0.1
DHCP_ACTIVE=true
DHCP_START=${DHCP_RANGE_START}
DHCP_END=${DHCP_RANGE_END}
DHCP_ROUTER=${LAN_ADDR}
DHCP_LEASETIME=12
PIHOLE_DOMAIN=lan
DHCP_IPv6=false
DNSSEC=false
REV_SERVER=false
EOF
        export PIHOLE_SKIP_OS_CHECK="${PIHOLE_SKIP_OS_CHECK:-true}"
        if ! curl -sSL https://install.pi-hole.net | bash /dev/stdin --unattended; then
            die "Pi-hole install failed — check network and re-run LAN setup"
        fi
        ok "Pi-hole installed"
    else
        ok "Pi-hole already installed"
    fi

    # v5/v6 post-config via FTL CLI when available
    if command -v pihole-FTL >/dev/null 2>&1; then
        info "Configuring Pi-hole DNS + DHCP on ${LAN_IF}…"
        # Force clean upstreams even if an older setupVars left "#name" tags.
        pihole-FTL --config dns.upstreams '[ "1.1.1.1", "1.0.0.1" ]' 2>/dev/null || true
        pihole-FTL --config dhcp.active true 2>/dev/null || true
        pihole-FTL --config dhcp.start "${DHCP_RANGE_START}" 2>/dev/null || true
        pihole-FTL --config dhcp.end "${DHCP_RANGE_END}" 2>/dev/null || true
        pihole-FTL --config dhcp.router "${LAN_ADDR}" 2>/dev/null || true
        pihole-FTL --config dhcp.leasetime "12h" 2>/dev/null \
            || pihole-FTL --config dhcp.leaseTime "12h" 2>/dev/null || true
        # Prefer the real LAN iface; LOCAL mode still answers DNS on other local
        # subnets (e.g. admin login via wlan/WAN IP for diagnosis).
        # DHCP is hard-blocked on WAN ifaces via no-dhcp-interface (below).
        pihole-FTL --config dns.interface "${LAN_IF}" 2>/dev/null \
            || pihole-FTL --config interface "${LAN_IF}" 2>/dev/null || true
        pihole-FTL --config dns.listeningMode LOCAL 2>/dev/null \
            || pihole-FTL --config dns.listeningMode local 2>/dev/null || true
        # Keep port 80 free for SCCS nginx — admin on 8080
        pihole-FTL --config webserver.port "8080o,[::]:8080o" 2>/dev/null \
            || pihole-FTL --config webserver.port "8080" 2>/dev/null || true
        # Load /etc/dnsmasq.d/*.conf (WAN DHCP block + screen reservations).
        pihole-FTL --config misc.etc_dnsmasq_d true 2>/dev/null || true

        # Dual-homed Pi: FTL listens on * for DHCP under LOCAL mode, then logs
        # "no address range available for DHCP request via wlan0" when home-LAN
        # broadcasts arrive. no-dhcp-interface disables DHCP on WAN only (DNS OK).
        {
            echo "# Generated by SCCS install.sh — do not edit by hand"
            echo "# DHCP only on ${LAN_IF} (${LAN_ADDR}/${LAN_CIDR}). Never lease on WAN."
            for w in "${WAN_LIST[@]}"; do
                echo "no-dhcp-interface=${w}"
            done
        } >"$PIHOLE_WAN_EXCEPT"
        chmod 644 "$PIHOLE_WAN_EXCEPT"
        ok "Pi-hole DHCP disabled on WAN: ${WAN_LIST[*]}"

        ok "Pi-hole DHCP ${DHCP_RANGE_START}–${DHCP_RANGE_END}, router ${LAN_ADDR}"
        info "Pi-hole admin UI: http://${LAN_ADDR}:8080/admin (or http://<wlan-ip>:8080/admin)"
        info "Admin on port 8080 so SCCS nginx can use :80"
    fi

    # Static DHCP from [screens] only when host+mac are already filled in (often empty on first install)
    apply_pihole_screen_reservations "$SCCS_CONF"

    # Web password: set once and record for the operator
    if [[ ! -f "$PIHOLE_PASS_FILE" ]]; then
        local ppass
        ppass="$(openssl rand -base64 15 | tr -d '/+=' | head -c 16)"
        if pihole setpassword "$ppass" 2>/dev/null \
            || pihole -a -p "$ppass" 2>/dev/null; then
            printf '%s\n' "$ppass" >"$PIHOLE_PASS_FILE"
            chmod 600 "$PIHOLE_PASS_FILE"
            ok "Pi-hole web password saved to $PIHOLE_PASS_FILE"
            warn "Pi-hole admin password: ${ppass}"
        else
            warn "Could not auto-set Pi-hole password — run: pihole setpassword"
        fi
    else
        info "Pi-hole web password file already exists: $PIHOLE_PASS_FILE"
    fi

    if command -v pihole >/dev/null 2>&1; then
        pihole reloaddns 2>/dev/null \
            || pihole restartdns 2>/dev/null \
            || systemctl restart pihole-FTL 2>/dev/null \
            || true
    else
        systemctl restart pihole-FTL 2>/dev/null || true
    fi
    ok "Pi-hole DNS + DHCP active on ${LAN_IF}"
    if [[ "$dhcp_newly_active" -eq 1 ]]; then
        mkdir -p "$INSTALL_RESUME_DIR"
        : >"$DHCP_PANEL_RESTART_FLAG"
    fi
}

# Write Pi-hole static DHCP from [screens] host+mac (safe if none configured yet).
# Pi-hole v6: use FTL dhcp.hosts only (not also dhcp-host in dnsmasq.d — that duplicates
# and FTL rejects the config, leaving the old dynamic lease in place).
apply_pihole_screen_reservations() {
    local conf_path="${1:-$CONF}"
    local PIHOLE_STATIC="/etc/dnsmasq.d/99-sccs-static-dhcp.conf"
    local LEASE_FILE="/etc/pihole/dhcp.leases"
    local name rest ip mac row hosts_json first
    local -a RESERVATIONS=() MACS_TO_DROP=()

    mapfile -t RESERVATIONS < <(python3 - "$conf_path" "${LAN_ADDR:-10.10.10.1}" <<'PY'
import configparser, ipaddress, re, subprocess, sys
path, lan_addr = sys.argv[1], sys.argv[2]
cfg = configparser.ConfigParser()
cfg.read(path)
if not cfg.has_section("screens"):
    sys.exit(0)

# Never publish DHCP statics that collide with this host Pi.
blocked = set()
try:
    blocked.add(ipaddress.IPv4Address(lan_addr))
except Exception:
    pass
try:
    out = subprocess.check_output(
        ["ip", "-4", "-o", "addr", "show", "scope", "global"],
        text=True, timeout=3,
    )
    for line in out.splitlines():
        for tok in line.split():
            if "/" in tok:
                try:
                    blocked.add(ipaddress.IPv4Address(tok.split("/", 1)[0]))
                except Exception:
                    pass
except Exception:
    pass

mac_re = re.compile(r"^([0-9a-f]{2}:){5}[0-9a-f]{2}$")
seen_ips = {}
for name, line in cfg.items("screens"):
    parts = [p.strip() for p in str(line).split("|")]
    if len(parts) < 11:
        continue
    host, mac = parts[2], parts[10].strip().lower()
    if not mac or mac in ("none", "-") or not mac_re.match(mac):
        continue
    try:
        ip_obj = ipaddress.IPv4Address(host)
        ip = str(ip_obj)
    except Exception:
        print(f"skip {name}: host must be IPv4", file=sys.stderr)
        continue
    if ip_obj in blocked:
        print(f"skip {name}: host {ip} is this Pi — refusing DHCP reservation", file=sys.stderr)
        continue
    if ip in seen_ips:
        print(f"skip {name}: host {ip} already reserved for {seen_ips[ip]}", file=sys.stderr)
        continue
    seen_ips[ip] = name
    print(f"{name}\t{ip}\t{mac}")
PY
)

    # Documentation-only drop-in (no dhcp-host lines — FTL dhcp.hosts owns reservations).
    # Keep the file so older installs overwrite any corrupt dhcp-host + leaked "info" lines.
    {
        echo "# Generated by SCCS install.sh from ${conf_path}"
        echo "# Static DHCP is applied via: pihole-FTL --config dhcp.hosts [...]"
        echo "# Do not put dhcp-host= here when using Pi-hole v6 (duplicates break FTL)."
        if [[ ${#RESERVATIONS[@]} -gt 0 ]]; then
            for row in "${RESERVATIONS[@]}"; do
                name="${row%%$'\t'*}"
                rest="${row#*$'\t'}"
                ip="${rest%%$'\t'*}"
                mac="${rest##*$'\t'}"
                echo "# reserve ${name}: ${mac} → ${ip}"
            done
        else
            echo "# (none yet — add host + mac under [screens], then re-run menu 6 or 8)"
        fi
    } >"$PIHOLE_STATIC"
    chmod 644 "$PIHOLE_STATIC"

    if [[ ${#RESERVATIONS[@]} -gt 0 ]]; then
        for row in "${RESERVATIONS[@]}"; do
            name="${row%%$'\t'*}"
            rest="${row#*$'\t'}"
            ip="${rest%%$'\t'*}"
            mac="${rest##*$'\t'}"
            info "  Pi-hole reserve ${name}: ${mac} → ${ip}"
            MACS_TO_DROP+=("$mac")
        done
    fi

    # Apply via FTL native config (Pi-hole v6)
    if command -v pihole-FTL >/dev/null 2>&1; then
        if [[ ${#RESERVATIONS[@]} -gt 0 ]]; then
            hosts_json="["
            first=1
            for row in "${RESERVATIONS[@]}"; do
                name="${row%%$'\t'*}"
                rest="${row#*$'\t'}"
                ip="${rest%%$'\t'*}"
                mac="${rest##*$'\t'}"
                if [[ "$first" -eq 1 ]]; then first=0; else hosts_json+=","; fi
                # mac,ip,hostname — infinite lease implied for static hosts
                hosts_json+="\"${mac},${ip},${name}\""
            done
            hosts_json+="]"
            if ! pihole-FTL --config dhcp.hosts "$hosts_json"; then
                warn "pihole-FTL --config dhcp.hosts failed — check: sudo pihole-FTL --config dhcp.hosts"
            else
                ok "FTL dhcp.hosts updated"
            fi
        else
            pihole-FTL --config dhcp.hosts '[]' 2>/dev/null || true
        fi
    fi

    # Drop dynamic leases for reserved MACs so the next DHCPREQUEST gets the static IP
    if [[ -f "$LEASE_FILE" && ${#MACS_TO_DROP[@]} -gt 0 ]]; then
        local tmp_leases mac_pat
        tmp_leases="$(mktemp)"
        mac_pat="$(printf '%s\n' "${MACS_TO_DROP[@]}" | paste -sd'|' -)"
        if awk -v pat="$mac_pat" 'BEGIN{IGNORECASE=1} $2 ~ ("^(" pat ")$") {next} {print}' \
            "$LEASE_FILE" >"$tmp_leases"; then
            if ! cmp -s "$LEASE_FILE" "$tmp_leases" 2>/dev/null; then
                cat "$tmp_leases" >"$LEASE_FILE"
                info "Cleared old dynamic lease(s) for reserved MAC(s) — panel must renew DHCP"
            fi
        fi
        rm -f "$tmp_leases"
    fi

    # Pi-hole v6 removed `restartdns` — prefer reloaddns, fall back to service restart
    if command -v pihole >/dev/null 2>&1; then
        pihole reloaddns 2>/dev/null \
            || pihole restartdns 2>/dev/null \
            || systemctl restart pihole-FTL 2>/dev/null \
            || true
    else
        systemctl restart pihole-FTL 2>/dev/null || true
    fi

    if [[ ${#RESERVATIONS[@]} -gt 0 ]]; then
        ok "Pi-hole DHCP reservations: ${#RESERVATIONS[@]} from [screens]"
        info "If a panel still has the old IP: reboot it, or on the panel: sudo dhclient -r && sudo dhclient"
    else
        info "Pi-hole DHCP pool only (no static reservations yet)"
    fi
}

# ---------------------------------------------------------------------------
# Prompt helpers for panel SSH credentials
# ---------------------------------------------------------------------------
prompt_secret() {
    # Same terminal as the other installer prompts. Reading stdin misses the
    # password when sudo or a previous ssh still owns that stream.
    read_secret_tty "$@" || true
}

# Sets REPLY_USER and REPLY_PASS (password not echoed).
# The password is not written into sccs.conf. Callers keep it with
# save_screen_credentials so later setup and package updates can reuse it.
prompt_panel_ssh_credentials() {
    local def_user="${1:-$USERNAME}" host_hint="${2:-panel}"
    local user pass
    echo
    info "SSH login for ${C_BOLD}${host_hint}${C_RESET}"
    info "The password is saved for this panel and reused for sudo (package updates and re-setup)."
    info "It is not written into sccs.conf."
    read -r -p "  SSH username [${def_user}]: " user || true
    user="${user:-$def_user}"
    [[ -n "$user" ]] || die "SSH username required"
    while true; do
        prompt_secret "SSH password for ${user}@${host_hint}: " pass
        if [[ -z "$pass" ]]; then
            warn "Password cannot be empty (needed for first-time setup)"
            continue
        fi
        break
    done
    REPLY_USER="$user"
    REPLY_PASS="$pass"
}

# Panel account passwords live in the install user's ~/.sccs, mode 600.
# sccs.conf is on the Samba share and is read by the UI, so the password stays out of it.
screen_credentials_path() {
    printf '%s\n' "${USER_HOME}/.sccs/screen_credentials"
}

_screen_credentials_b64() {
    printf '%s' "$1" | base64 -w 0 2>/dev/null || printf '%s' "$1" | base64 | tr -d '\n'
}

# Args: name host user password. Replaces any record for this screen name or host.
save_screen_credentials() {
    local name="$1" host="$2" user="$3" pass="$4"
    local path dir tmp b64 line n h rest
    [[ -n "${USER_HOME:-}" && -n "${USERNAME:-}" ]] || return 1
    [[ -n "$name" && -n "$host" && -n "$user" && -n "$pass" ]] || return 0
    [[ "$name" =~ ^[a-z][a-z0-9_]*$ ]] || return 1
    [[ "$host" =~ ^[A-Za-z0-9][-A-Za-z0-9._]*$ ]] || return 1
    [[ "$user" =~ ^[A-Za-z_][-A-Za-z0-9_]*$ ]] || return 1
    path="$(screen_credentials_path)"
    dir="$(dirname "$path")"
    mkdir -p "$dir"
    chown "$USERNAME":"$USERNAME" "$dir" 2>/dev/null || true
    chmod 700 "$dir"
    b64="$(_screen_credentials_b64 "$pass")"
    [[ -n "$b64" ]] || return 1
    tmp="$(mktemp)"
    if [[ -f "$path" ]]; then
        while IFS= read -r line || [[ -n "$line" ]]; do
            [[ -z "$line" || "$line" == \#* ]] && continue
            read -r n h rest <<<"$line"
            [[ "$n" == "$name" || "$h" == "$host" ]] && continue
            printf '%s\n' "$line" >>"$tmp"
        done <"$path"
    fi
    printf '%s %s %s %s\n' "$name" "$host" "$user" "$b64" >>"$tmp"
    chown "$USERNAME":"$USERNAME" "$tmp" 2>/dev/null || true
    chmod 600 "$tmp"
    mv -f "$tmp" "$path"
    chown "$USERNAME":"$USERNAME" "$path" 2>/dev/null || true
    chmod 600 "$path"
}

# Args: name. Drops the stored password for that screen.
forget_screen_credentials() {
    local name="$1"
    local path tmp line n rest
    [[ -n "${USER_HOME:-}" && -n "${USERNAME:-}" ]] || return 0
    [[ "$name" =~ ^[a-z][a-z0-9_]*$ ]] || return 0
    path="$(screen_credentials_path)"
    [[ -f "$path" ]] || return 0
    tmp="$(mktemp)"
    while IFS= read -r line || [[ -n "$line" ]]; do
        [[ -z "$line" || "$line" == \#* ]] && continue
        read -r n rest <<<"$line"
        [[ "$n" == "$name" ]] && continue
        printf '%s\n' "$line" >>"$tmp"
    done <"$path"
    chown "$USERNAME":"$USERNAME" "$tmp" 2>/dev/null || true
    chmod 600 "$tmp"
    mv -f "$tmp" "$path"
    chown "$USERNAME":"$USERNAME" "$path" 2>/dev/null || true
    chmod 600 "$path"
}

# Args: name host user. Prefer the screen name, then host+user.
# Sets REPLY_PASS and REPLY_CRED_USER. Returns 0 when a password is stored.
load_screen_password() {
    local name="$1" host="$2" user="$3"
    local path line n h u b64 decoded by_host="" by_user=""
    REPLY_PASS=""
    REPLY_CRED_USER=""
    path="$(screen_credentials_path)"
    [[ -f "$path" ]] || return 1
    while IFS= read -r line || [[ -n "$line" ]]; do
        [[ -z "$line" || "$line" == \#* ]] && continue
        read -r n h u b64 <<<"$line"
        [[ -n "${b64:-}" ]] || continue
        decoded="$(printf '%s' "$b64" | base64 -d 2>/dev/null || true)"
        [[ -n "$decoded" ]] || continue
        if [[ -n "$name" && "$n" == "$name" ]]; then
            REPLY_PASS="$decoded"
            REPLY_CRED_USER="$u"
            return 0
        fi
        if [[ -n "$host" && "$h" == "$host" && ( -z "$user" || "$u" == "$user" ) ]]; then
            by_host="$decoded"
            by_user="$u"
        fi
    done <"$path"
    if [[ -n "$by_host" ]]; then
        REPLY_PASS="$by_host"
        REPLY_CRED_USER="$by_user"
        return 0
    fi
    return 1
}

# One SSH login attempt (publickey, else password). Does not prompt.
# Args: user host [password]
# Prints AUTH=key|password on success (to captured stdout of child); returns 0/1.
_try_panel_ssh_once() {
    local screen_user="$1" screen_host="$2" screen_pass="${3:-}"
    local err=""

    # || true: capture failure without aborting the installer (set -e)
    err="$(
        run_user_bash env \
            SCREEN_USER="$screen_user" \
            SCREEN_HOST="$screen_host" \
            SCREEN_PASS="${screen_pass:-}" \
            <<'EOS' 2>&1
set -euo pipefail
KEY="$HOME/.ssh/sccs_screen"
KNOWN_HOSTS="$HOME/.sccs/screen_known_hosts"
mkdir -p "$HOME/.ssh" "$(dirname "$KNOWN_HOSTS")"
chmod 700 "$HOME/.ssh"
SSH_COMMON=(
    -o UserKnownHostsFile="${KNOWN_HOSTS}"
    -o StrictHostKeyChecking=accept-new
    -o ConnectTimeout=10
    -o ServerAliveInterval=5
    -o LogLevel=ERROR
)

# 1) Existing key?
if [[ -f "$KEY" ]]; then
    if ssh "${SSH_COMMON[@]}" -o BatchMode=yes \
        -o PreferredAuthentications=publickey -o IdentitiesOnly=yes \
        -i "$KEY" "${SCREEN_USER}@${SCREEN_HOST}" 'echo sccs-ssh-ok' 2>/dev/null \
        | grep -qx 'sccs-ssh-ok'; then
        echo "AUTH=key"
        exit 0
    fi
fi

# 2) Password
if [[ -z "${SCREEN_PASS:-}" ]]; then
    echo "AUTH_FAIL=no-password-and-key-failed"
    exit 1
fi
PASSFILE="$(mktemp)"; ASKPASS="$(mktemp)"
chmod 600 "$PASSFILE"; chmod 700 "$ASKPASS"
printf '%s\n' "$SCREEN_PASS" >"$PASSFILE"
cat >"$ASKPASS" <<ASK
#!/bin/bash
cat $(printf '%q' "$PASSFILE")
ASK
set +e
out="$(
    setsid -w env \
        DISPLAY="${DISPLAY:-:0}" \
        SSH_ASKPASS="$ASKPASS" \
        SSH_ASKPASS_REQUIRE=force \
        ssh "${SSH_COMMON[@]}" \
            -o PreferredAuthentications=password,keyboard-interactive \
            -o PubkeyAuthentication=no \
            -o NumberOfPasswordPrompts=1 \
            -o BatchMode=no \
            "${SCREEN_USER}@${SCREEN_HOST}" 'echo sccs-ssh-ok' </dev/null 2>&1
)"
rc=$?
rm -f "$PASSFILE" "$ASKPASS"
set -e
if [[ "$rc" -eq 0 ]] && printf '%s\n' "$out" | tr -d '\r' | grep -qx 'sccs-ssh-ok'; then
    echo "AUTH=password"
    exit 0
fi
echo "$out" | grep -v '^$' | tail -n 5 | sed 's/^/SSH_ERR=/'
echo "AUTH_FAIL=1"
exit 1
EOS
    )" || true

    REPLY_SSH_DIAG="$err"
    if echo "$err" | grep -q '^AUTH='; then
        REPLY_SSH_AUTH="${err##*AUTH=}"
        REPLY_SSH_AUTH="${REPLY_SSH_AUTH%%$'\n'*}"
        return 0
    fi
    return 1
}

# Verify SSH with retry: re-prompt password (and optional username) or abort.
# Args: user host [password]
# On success: sets REPLY_USER REPLY_PASS and returns 0.
# On abort: returns 1 (caller must stop — no probe/provision).
verify_panel_ssh() {
    local screen_user="$1" screen_host="$2" screen_pass="${3:-}"
    local ans=""

    REPLY_USER="$screen_user"
    REPLY_PASS="$screen_pass"

    while true; do
        info "Verifying SSH login as ${REPLY_USER}@${screen_host}…"
        if _try_panel_ssh_once "$REPLY_USER" "$screen_host" "$REPLY_PASS"; then
            ok "SSH login works (${REPLY_SSH_AUTH:-ok})"
            return 0
        fi

        fail "SSH login failed for ${REPLY_USER}@${screen_host}"
        if echo "${REPLY_SSH_DIAG:-}" | grep -q 'AUTH_FAIL=no-password'; then
            info "No password provided and publickey auth did not work."
        fi
        while IFS= read -r line; do
            [[ "$line" == SSH_ERR=* ]] && info "${line#SSH_ERR=}"
        done <<<"${REPLY_SSH_DIAG:-}"
        info "Check username/password, reachability, and that sshd allows PasswordAuthentication."
        echo
        echo "  ${C_CYAN}r${C_RESET}  Retry — enter password again"
        echo "  ${C_CYAN}u${C_RESET}  Retry — change username and password"
        echo "  ${C_CYAN}a${C_RESET}  Abort setup for this screen"
        read -r -p "  Choice [r]: " ans || true
        ans="${ans:-r}"
        case "${ans,,}" in
            a|abort|q|quit|n|no)
                warn "SSH setup aborted"
                unset REPLY_PASS 2>/dev/null || true
                return 1
                ;;
            u|user|username)
                read -r -p "  SSH username [${REPLY_USER}]: " ans || true
                REPLY_USER="${ans:-$REPLY_USER}"
                [[ -n "$REPLY_USER" ]] || { warn "Username required"; continue; }
                prompt_secret "SSH password for ${REPLY_USER}@${screen_host}: " REPLY_PASS
                if [[ -z "$REPLY_PASS" ]]; then
                    warn "Password cannot be empty"
                    continue
                fi
                ;;
            r|retry|p|password|""|y|yes)
                prompt_secret "SSH password for ${REPLY_USER}@${screen_host}: " REPLY_PASS
                if [[ -z "$REPLY_PASS" ]]; then
                    warn "Password cannot be empty"
                    continue
                fi
                ;;
            *)
                warn "Pick r (retry password), u (change user), or a (abort)"
                ;;
        esac
    done
}

# ---------------------------------------------------------------------------
# Probe a panel over SSH for brightness / blank control paths.
# Sets: REPLY_BRIGHT REPLY_BLANK REPLY_METHOD REPLY_PROBE_NOTES
# Uses publickey if available, otherwise password via SSH_ASKPASS.
# Args: user host [password]
# ---------------------------------------------------------------------------
probe_panel_display_controls() {
    local screen_user="$1" screen_host="$2" screen_pass="${3:-}"
    local out=""

    REPLY_BRIGHT=""
    REPLY_BLANK=""
    REPLY_METHOD=""
    REPLY_PROBE_NOTES=""

    info "Querying display controls on ${screen_user}@${screen_host}…"

    out="$(
        run_user_bash env \
            SCREEN_USER="$screen_user" \
            SCREEN_HOST="$screen_host" \
            SCREEN_PASS="${screen_pass:-}" \
            <<'EOS'
set -euo pipefail
KEY="$HOME/.ssh/sccs_screen"
KNOWN_HOSTS="$HOME/.sccs/screen_known_hosts"
mkdir -p "$HOME/.ssh" "$(dirname "$KNOWN_HOSTS")"
chmod 700 "$HOME/.ssh"

SSH_COMMON=(
    -o UserKnownHostsFile="${KNOWN_HOSTS}"
    -o StrictHostKeyChecking=accept-new
    -o ConnectTimeout=12
    -o ServerAliveInterval=5
    -o LogLevel=ERROR
)

REMOTE_PROBE=$(cat <<'REMOTE'
set +e
uid="$(id -u)"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/${uid}}"
if [[ -z "${DBUS_SESSION_BUS_ADDRESS:-}" && -S "${XDG_RUNTIME_DIR}/bus" ]]; then
    export DBUS_SESSION_BUS_ADDRESS="unix:path=${XDG_RUNTIME_DIR}/bus"
fi
for w in wayland-0 wayland-1; do
    if [[ -S "${XDG_RUNTIME_DIR}/${w}" ]]; then
        export WAYLAND_DISPLAY="$w"
        break
    fi
done

# --- KDE ScreenBrightness display objects ---
if command -v busctl >/dev/null 2>&1; then
    tree="$(busctl --user tree org.kde.ScreenBrightness 2>/dev/null || true)"
    if [[ -n "$tree" ]]; then
        printf '%s\n' "$tree" | grep -oE '/org/kde/ScreenBrightness/display[0-9]+' | sort -u | while read -r obj; do
            if busctl --user get-property org.kde.ScreenBrightness "$obj" \
                org.kde.ScreenBrightness.Display MaxBrightness >/dev/null 2>&1; then
                echo "KDE_OBJ=${obj}"
            fi
        done
    else
        for obj in /org/kde/ScreenBrightness/display0 /org/kde/ScreenBrightness/display1; do
            if busctl --user get-property org.kde.ScreenBrightness "$obj" \
                org.kde.ScreenBrightness.Display MaxBrightness >/dev/null 2>&1; then
                echo "KDE_OBJ=${obj}"
            fi
        done
    fi
fi

# --- kscreen-doctor connected outputs ---
if command -v kscreen-doctor >/dev/null 2>&1; then
    kscreen-doctor -o 2>/dev/null | while IFS= read -r line; do
        name=""
        if [[ "$line" =~ Output:[[:space:]]+[0-9]+[[:space:]]+([^[:space:]]+) ]]; then
            name="${BASH_REMATCH[1]}"
        elif [[ "$line" =~ ^[[:space:]]*([A-Za-z0-9][-A-Za-z0-9.]+)[[:space:]]+.*connected ]]; then
            name="${BASH_REMATCH[1]}"
        fi
        [[ -z "$name" ]] && continue
        if echo "$line" | grep -qiE 'disconnected'; then
            continue
        fi
        echo "KSCREEN_OUT=${name}"
    done
fi

# --- wlr-randr (labwc / Raspberry Pi OS Wayland) ---
if command -v wlr-randr >/dev/null 2>&1; then
    export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
    export WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-wayland-0}"
    wlr-randr 2>/dev/null | awk '
        /^[^ ]/ { cur=$1 }
        cur != "" && /Enabled:/ { print "WLR_OUT=" cur }
    '
fi

# --- sysfs backlight devices ---
if compgen -G '/sys/class/backlight/*/brightness' >/dev/null 2>&1; then
    for b in /sys/class/backlight/*/brightness; do
        [[ -e "$b" ]] || continue
        dir="$(dirname "$b")"
        maxf="${dir}/max_brightness"
        maxv=""
        [[ -r "$maxf" ]] && maxv="$(cat "$maxf" 2>/dev/null || true)"
        echo "BACKLIGHT=${b}${maxv:+|max=${maxv}}"
    done
fi

# --- framebuffer blank nodes ---
if compgen -G '/sys/class/graphics/fb*/blank' >/dev/null 2>&1; then
    for f in /sys/class/graphics/fb*/blank; do
        [[ -e "$f" ]] && echo "FB_BLANK=${f}"
    done
fi

# --- freedesktop screensaver ---
if command -v busctl >/dev/null 2>&1; then
    if busctl --user get-property org.freedesktop.ScreenSaver \
        /org/freedesktop/ScreenSaver org.freedesktop.ScreenSaver Active \
        >/dev/null 2>&1; then
        echo "SCREENSAVER=org.freedesktop.ScreenSaver"
    fi
fi

echo "HOSTNAME=$(hostname -s 2>/dev/null || hostname 2>/dev/null || true)"
if [[ -n "${XDG_CURRENT_DESKTOP:-}" ]]; then
    echo "DESKTOP=${XDG_CURRENT_DESKTOP}"
elif [[ -n "${DESKTOP_SESSION:-}" ]]; then
    echo "DESKTOP=${DESKTOP_SESSION}"
fi
echo "PROBE_OK=1"
REMOTE
)

run_remote() {
    local mode="$1"
    if [[ "$mode" == "key" ]]; then
        [[ -f "$KEY" ]] || return 1
        ssh "${SSH_COMMON[@]}" -o BatchMode=yes \
            -o PreferredAuthentications=publickey -o IdentitiesOnly=yes \
            -i "$KEY" "${SCREEN_USER}@${SCREEN_HOST}" bash -s <<<"$REMOTE_PROBE"
        return $?
    fi
    [[ -n "${SCREEN_PASS:-}" ]] || return 1
    local PASSFILE ASKPASS
    PASSFILE="$(mktemp)"; ASKPASS="$(mktemp)"
    chmod 600 "$PASSFILE"; chmod 700 "$ASKPASS"
    printf '%s\n' "$SCREEN_PASS" >"$PASSFILE"
    cat >"$ASKPASS" <<ASK
#!/bin/bash
cat $(printf '%q' "$PASSFILE")
ASK
    local rc=0
    setsid -w env \
        DISPLAY="${DISPLAY:-:0}" \
        SSH_ASKPASS="$ASKPASS" \
        SSH_ASKPASS_REQUIRE=force \
        ssh "${SSH_COMMON[@]}" \
            -o PreferredAuthentications=password,keyboard-interactive \
            -o PubkeyAuthentication=no \
            -o NumberOfPasswordPrompts=1 \
            "${SCREEN_USER}@${SCREEN_HOST}" bash -s <<<"$REMOTE_PROBE" || rc=$?
    rm -f "$PASSFILE" "$ASKPASS"
    return $rc
}

if ! run_remote key 2>/dev/null; then
    if ! run_remote pass; then
        echo "PROBE_FAIL=1" >&2
        exit 1
    fi
fi
EOS
    )" || {
        warn "Could not query display controls on ${screen_host}"
        REPLY_BRIGHT="/sys/class/graphics/fb0/blank"
        REPLY_BLANK="/sys/class/graphics/fb0/blank"
        REPLY_METHOD="fallback-fb"
        REPLY_PROBE_NOTES="probe failed — using framebuffer blank defaults"
        return 1
    }

    local pick
    pick="$(printf '%s\n' "$out" | python3 -c '
import sys
lines = sys.stdin.read().splitlines()
kde, kscreen, wlr, backlight, fb = [], [], [], [], []
desktop = host = ""
for line in lines:
    line = line.strip()
    if not line or "=" not in line:
        continue
    k, v = line.split("=", 1)
    if k == "KDE_OBJ":
        kde.append(v)
    elif k == "KSCREEN_OUT":
        if v not in kscreen:
            kscreen.append(v)
    elif k == "WLR_OUT":
        if v not in wlr:
            wlr.append(v)
    elif k == "BACKLIGHT":
        backlight.append(v.split("|", 1)[0])
    elif k == "FB_BLANK":
        fb.append(v)
    elif k == "DESKTOP":
        desktop = v
    elif k == "HOSTNAME":
        host = v
    elif k == "PROBE_FAIL":
        print("FAIL\t\t\tprobe failed")
        raise SystemExit(0)

detail = []
if host:
    detail.append("host=" + host)
if desktop:
    detail.append("desktop=" + desktop)

# Prefer KDE, then wlr-randr (labwc / Pi OS Wayland HDMI panels), then backlight/fb
if kde:
    bright = "dbus:org.kde.ScreenBrightness:" + kde[0]
    if kscreen:
        blank = "kscreen:" + kscreen[0]
        method = "kde+kscreen"
        detail.append("kscreen outs: " + ", ".join(kscreen))
    elif wlr:
        blank = "wlr:" + wlr[0]
        method = "kde+wlr"
        detail.append("wlr outs: " + ", ".join(wlr))
    elif fb:
        blank = fb[0]
        method = "kde+fb"
    else:
        blank = "none"
        method = "kde"
    detail.append("kde objs: " + ", ".join(kde))
elif wlr:
    # Binary on/off via wlr-randr (WaveShare HDMI under labwc, etc.)
    bright = "wlr:" + wlr[0]
    blank = "wlr:" + wlr[0]
    method = "wlr-randr"
    detail.append("wlr outs: " + ", ".join(wlr))
elif backlight:
    bright = backlight[0]
    blank = fb[0] if fb else "none"
    method = "sysfs-backlight"
    if len(backlight) > 1:
        detail.append("backlights: " + ", ".join(backlight))
elif fb:
    bright = fb[0]
    blank = fb[0]
    method = "sysfs-fb-blank"
else:
    bright = "/sys/class/graphics/fb0/blank"
    blank = "/sys/class/graphics/fb0/blank"
    method = "fallback-fb"
    detail.append("no display controls found")

print(bright + "\t" + blank + "\t" + method + "\t" + "; ".join(detail))
')"

    if [[ -z "$pick" || "$pick" == FAIL* ]]; then
        warn "Display probe returned nothing useful"
        REPLY_BRIGHT="/sys/class/graphics/fb0/blank"
        REPLY_BLANK="/sys/class/graphics/fb0/blank"
        REPLY_METHOD="fallback-fb"
        REPLY_PROBE_NOTES="empty probe"
        return 1
    fi

    IFS=$'\t' read -r REPLY_BRIGHT REPLY_BLANK REPLY_METHOD REPLY_PROBE_NOTES <<<"$pick"
    ok "Detected ${REPLY_METHOD}: brightness=${REPLY_BRIGHT}"
    info "blank_path=${REPLY_BLANK}${REPLY_PROBE_NOTES:+  (${REPLY_PROBE_NOTES})}"
    return 0
}


# ---------------------------------------------------------------------------
# Integrated: one touchscreen (SSH key + blank/shutdown sudoers + desktop autologin + Chromium UI)
# Args: user host alias blank_path skip_blank(0|1) [password] [reserved_ip]
# host          = address that answers now (live lease or already-moved reserved)
# reserved_ip   = desired/static IP written to conf + Pi-hole (may equal host)
# Password optional if key auth already works; otherwise prompted.
# Key material + ssh run as USERNAME (not root).
# The panel account password is stored in ~/.sccs/screen_credentials (mode 600),
# not in sccs.conf. Optional arg 8 is the [screens] key used for that file.
# On success, enables passwordless desktop login on Raspberry Pi OS and Armbian,
# turns off idle display blanking and sleep, sets the Chromium homepage to the
# control Pi, and autostarts it.
# When reserved_ip differs from the live lease, the panel is told to drop that
# lease. Later steps (Chromium) run only after the reserved address answers.
# ---------------------------------------------------------------------------
configure_touchscreen_panel() {
    local screen_user="$1" screen_host="$2" screen_alias="$3"
    local blank_path="${4:-/sys/class/graphics/fb0/blank}" skip_blank="${5:-0}"
    local screen_pass="${6:-}" reserved_ip="${7:-}" screen_name="${8:-}"
    local rc=0 live_host

    if [[ -z "$screen_pass" && -n "$screen_name" ]]; then
        if load_screen_password "$screen_name" "${reserved_ip:-$screen_host}" "$screen_user"; then
            screen_pass="$REPLY_PASS"
            [[ -n "${REPLY_CRED_USER:-}" ]] && screen_user="$REPLY_CRED_USER"
            info "Using the saved panel password for ${screen_name}"
        fi
        REPLY_PASS=""
    fi

    # Live connection target for key/sudoers install (may still be old lease).
    live_host="$screen_host"
    # Prefer reserved/desired IP when it is already the live host.
    if [[ -n "$reserved_ip" && "$reserved_ip" != "$screen_host" ]]; then
        if panel_ssh_port_open "$reserved_ip" 2; then
            info "Reserved IP ${reserved_ip} is already reachable — using it for SSH setup"
            screen_host="$reserved_ip"
            live_host="$reserved_ip"
        else
            info "SSH setup via ${screen_host}; reserved target is ${reserved_ip}"
        fi
    fi

    # Fast path: already passwordless?
    if [[ -z "$screen_pass" ]]; then
        if run_user_bash env \
            SCREEN_USER="$screen_user" \
            SCREEN_HOST="$screen_host" \
            <<'EOCHECK'
set -euo pipefail
KEY="$HOME/.ssh/sccs_screen"
KNOWN_HOSTS="$HOME/.sccs/screen_known_hosts"
mkdir -p "$HOME/.ssh" "$(dirname "$KNOWN_HOSTS")"
chmod 700 "$HOME/.ssh"
[[ -f "$KEY" ]] || exit 2
ssh -o BatchMode=yes -o PreferredAuthentications=publickey \
    -o IdentitiesOnly=yes \
    -o UserKnownHostsFile="${KNOWN_HOSTS}" \
    -o StrictHostKeyChecking=accept-new \
    -o ConnectTimeout=8 \
    -i "$KEY" "${SCREEN_USER}@${SCREEN_HOST}" 'echo ok' >/dev/null 2>&1
EOCHECK
        then
            info "Passwordless SSH already works for ${screen_user}@${screen_host}"
        else
            info "Need the panel password to install the SCCS SSH key and sudo rules"
            prompt_panel_ssh_credentials "$screen_user" "${screen_host}"
            screen_user="$REPLY_USER"
            screen_pass="$REPLY_PASS"
        fi
    fi
    if [[ -n "$screen_name" && -n "$screen_pass" ]]; then
        save_screen_credentials "$screen_name" "${reserved_ip:-$screen_host}" \
            "$screen_user" "$screen_pass" || true
    fi

    # Note: do NOT invert with `!` — failure must yield non-zero rc (was a bug that always ✓'d).
    if run_user_bash env \
        SCREEN_USER="$screen_user" \
        SCREEN_HOST="$screen_host" \
        SCREEN_ALIAS="$screen_alias" \
        BLANK_PATH="$blank_path" \
        SKIP_BLANK="$skip_blank" \
        SCREEN_PASS="${screen_pass:-}" \
        <<'EOS'
set -euo pipefail
KEY="$HOME/.ssh/sccs_screen"
SSH_CONFIG="$HOME/.ssh/config"
KNOWN_HOSTS="$HOME/.sccs/screen_known_hosts"
SSH_COMMON=(
    -o UserKnownHostsFile="${KNOWN_HOSTS}"
    -o StrictHostKeyChecking=accept-new
    -o ConnectTimeout=15
    -o ServerAliveInterval=5
    -o LogLevel=ERROR
)
SSH_KEY=(
    "${SSH_COMMON[@]}"
    -o PreferredAuthentications=publickey
    -o IdentitiesOnly=yes
    -i "${KEY}"
)
SSH_BATCH=("${SSH_KEY[@]}" -o BatchMode=yes)

mkdir -p "$HOME/.ssh" "$(dirname "$KNOWN_HOSTS")"
chmod 700 "$HOME/.ssh"

if [[ ! -f "$KEY" ]]; then
    ssh-keygen -t ed25519 -f "$KEY" -N "" -C "sccs-screen-control"
    echo "Created $KEY"
fi

# Keep ~/.ssh/config entry in sync (host + alias + user).
_update_ssh_config() {
    local tmp
    mkdir -p "$(dirname "$SSH_CONFIG")"
    touch "$SSH_CONFIG"
    chmod 600 "$SSH_CONFIG"
    tmp="$(mktemp)"
    awk -v h="$SCREEN_HOST" -v a="$SCREEN_ALIAS" '
        BEGIN { skip=0 }
        /^Host[ \t]/ {
            skip=0
            for (i=2; i<=NF; i++) if ($i==h || $i==a) skip=1
            if (skip) next
        }
        skip && /^Host[ \t]/ { skip=0 }
        skip { next }
        { print }
    ' "$SSH_CONFIG" >"$tmp"
    cat >>"$tmp" <<EOF

# Screen control — ${SCREEN_HOST} (config/sccs.conf [screens])
Host ${SCREEN_HOST} ${SCREEN_ALIAS}
    HostName ${SCREEN_HOST}
    User ${SCREEN_USER}
    IdentityFile ~/.ssh/sccs_screen
    IdentitiesOnly yes
    StrictHostKeyChecking accept-new
    UserKnownHostsFile ~/.sccs/screen_known_hosts
EOF
    mv "$tmp" "$SSH_CONFIG"
    chmod 600 "$SSH_CONFIG"
    echo "SSH config updated for ${SCREEN_USER}@${SCREEN_HOST}"
}
_update_ssh_config

key_ok() {
    ssh "${SSH_BATCH[@]}" "${SCREEN_USER}@${SCREEN_HOST}" 'echo ok' >/dev/null 2>&1
}

ASKPASS=""
PASSFILE=""
cleanup_askpass() {
    [[ -n "${ASKPASS:-}" && -f "${ASKPASS:-}" ]] && rm -f "$ASKPASS"
    [[ -n "${PASSFILE:-}" && -f "${PASSFILE:-}" ]] && rm -f "$PASSFILE"
    ASKPASS=""; PASSFILE=""
}
trap cleanup_askpass EXIT

setup_askpass() {
    cleanup_askpass
    [[ -n "${SCREEN_PASS:-}" ]] || return 1
    PASSFILE="$(mktemp)"
    ASKPASS="$(mktemp)"
    chmod 600 "$PASSFILE"
    chmod 700 "$ASKPASS"
    printf '%s\n' "$SCREEN_PASS" >"$PASSFILE"
    cat >"$ASKPASS" <<ASK
#!/bin/bash
cat $(printf '%q' "$PASSFILE")
ASK
}

# Password SSH for one-time key install (OpenSSH askpass — no interactive prompt).
ssh_password() {
    setup_askpass || return 1
    # setsid drops the terminal so the password comes from askpass, not from
    # whatever else is still on stdin (the installer script or a sudo pty).
    setsid -w env \
        DISPLAY="${DISPLAY:-:0}" \
        SSH_ASKPASS="$ASKPASS" \
        SSH_ASKPASS_REQUIRE=force \
        ssh \
            "${SSH_COMMON[@]}" \
            -o PreferredAuthentications=password,keyboard-interactive \
            -o PubkeyAuthentication=no \
            -o NumberOfPasswordPrompts=1 \
            -o BatchMode=no \
            "${SCREEN_USER}@${SCREEN_HOST}" \
            "$@" </dev/null
}

install_authorized_key() {
    local pub remote
    pub="$(cat "${KEY}.pub")"
    echo "Installing public key on ${SCREEN_USER}@${SCREEN_HOST}…"
    remote=$(cat <<EOF
set -euo pipefail
mkdir -p "\$HOME/.ssh"
chmod 700 "\$HOME/.ssh"
touch "\$HOME/.ssh/authorized_keys"
chmod 600 "\$HOME/.ssh/authorized_keys"
pub=$(printf '%q' "$pub")
if ! grep -Fqx "\$pub" "\$HOME/.ssh/authorized_keys" 2>/dev/null; then
    printf '%s\n' "\$pub" >> "\$HOME/.ssh/authorized_keys"
    echo "key-added"
else
    echo "key-present"
fi
EOF
)
    if ! ssh_password bash -c "$remote"; then
        echo "" >&2
        echo "Password login failed for ${SCREEN_USER}@${SCREEN_HOST}." >&2
        echo "Check username/password, host reachability, and that sshd allows PasswordAuthentication." >&2
        echo "Armbian: set PasswordAuthentication yes in sshd_config, restart ssh, retry." >&2
        exit 1
    fi
    echo "Verifying passwordless login…"
    # brief settle for sshd
    sleep 0.3
    if ! key_ok; then
        echo "Key installed but publickey login still failed." >&2
        return 1
    fi
    echo "Passwordless SSH OK"
}

if key_ok; then
    echo "Passwordless SSH already works for ${SCREEN_USER}@${SCREEN_HOST}"
else
    if [[ -z "${SCREEN_PASS:-}" ]]; then
        echo "No password provided and key auth failed." >&2
        exit 1
    fi
    install_authorized_key
fi

# --- sudoers for blank / shutdown ---
if [[ "${SKIP_BLANK}" == "1" ]]; then
    remote_cmd=$(cat <<EOF
set -euo pipefail
SHUTDOWN_SUDOERS='/etc/sudoers.d/sccs-screen-shutdown'
SHUTDOWN_LINE='${SCREEN_USER} ALL=(root) NOPASSWD: /sbin/shutdown -h now, /usr/sbin/shutdown -h now, /sbin/poweroff, /usr/sbin/poweroff'
install -d -m 755 /etc/sudoers.d
printf '%s\n' "\$SHUTDOWN_LINE" > "\$SHUTDOWN_SUDOERS"
chmod 440 "\$SHUTDOWN_SUDOERS"
visudo -cf "\$SHUTDOWN_SUDOERS"
visudo -c
echo "Installed shutdown sudoers"
EOF
)
else
    remote_cmd=$(cat <<EOF
set -euo pipefail
BLANK_PATH='${BLANK_PATH}'
BLANK_SUDOERS='/etc/sudoers.d/sccs-screen-blank'
SHUTDOWN_SUDOERS='/etc/sudoers.d/sccs-screen-shutdown'
BLANK_LINE='${SCREEN_USER} ALL=(root) NOPASSWD: /usr/bin/tee ${BLANK_PATH}'
SHUTDOWN_LINE='${SCREEN_USER} ALL=(root) NOPASSWD: /sbin/shutdown -h now, /usr/sbin/shutdown -h now, /sbin/poweroff, /usr/sbin/poweroff'
install -d -m 755 /etc/sudoers.d
printf '%s\n' "\$BLANK_LINE" > "\$BLANK_SUDOERS"
chmod 440 "\$BLANK_SUDOERS"
visudo -cf "\$BLANK_SUDOERS"
printf '%s\n' "\$SHUTDOWN_LINE" > "\$SHUTDOWN_SUDOERS"
chmod 440 "\$SHUTDOWN_SUDOERS"
visudo -cf "\$SHUTDOWN_SUDOERS"
visudo -c
echo "Installed blank + shutdown sudoers"
EOF
)
fi

echo "Installing passwordless sudo rules on ${SCREEN_USER}@${SCREEN_HOST}…"
if [[ -n "${SCREEN_PASS:-}" ]]; then
    # Key auth for SSH; pipe password into remote sudo -S (no second interactive prompt).
    if ! printf '%s\n' "$SCREEN_PASS" | ssh "${SSH_BATCH[@]}" "${SCREEN_USER}@${SCREEN_HOST}" \
        "sudo -S -p '' bash -c $(printf '%q' "$remote_cmd")"; then
        echo "sudoers install failed (wrong sudo password, or sudo denied)." >&2
        exit 1
    fi
else
    if ! ssh "${SSH_BATCH[@]}" "${SCREEN_USER}@${SCREEN_HOST}" \
        "sudo -n bash -c $(printf '%q' "$remote_cmd")" 2>/dev/null; then
        echo "Need the panel password for one-time sudo setup." >&2
        exit 2
    fi
fi

echo "Verifying remote control…"
if [[ "${SKIP_BLANK}" != "1" ]]; then
    ssh "${SSH_BATCH[@]}" "${SCREEN_USER}@${SCREEN_HOST}" \
        "sudo -n -l | grep -F '/usr/bin/tee ${BLANK_PATH}'" >/dev/null
fi
ssh "${SSH_BATCH[@]}" "${SCREEN_USER}@${SCREEN_HOST}" \
    "sudo -n -l | grep -E 'shutdown -h now|/sbin/poweroff|/usr/sbin/poweroff'" >/dev/null

cleanup_askpass
trap - EXIT
unset SCREEN_PASS

echo "Done — SCCS can control ${SCREEN_USER}@${SCREEN_HOST} over SSH."
EOS
    then
        rc=0
    else
        rc=$?
        [[ "$rc" -ne 0 ]] || rc=1
    fi

    # If sudo needed password but we only had key (exit 2), re-prompt and retry once.
    if [[ "$rc" -eq 2 ]]; then
        info "Sudo on the panel still needs a password once"
        prompt_panel_ssh_credentials "$screen_user" "${screen_host}"
        screen_user="$REPLY_USER"
        screen_pass="$REPLY_PASS"
        if [[ -n "$screen_name" && -n "$screen_pass" ]]; then
            save_screen_credentials "$screen_name" "${reserved_ip:-$screen_host}" \
                "$screen_user" "$screen_pass" || true
        fi
        if run_user_bash env \
            SCREEN_USER="$screen_user" \
            SCREEN_HOST="$screen_host" \
            SCREEN_ALIAS="$screen_alias" \
            BLANK_PATH="$blank_path" \
            SKIP_BLANK="$skip_blank" \
            SCREEN_PASS="$screen_pass" \
            <<'EOS2'
set -euo pipefail
KEY="$HOME/.ssh/sccs_screen"
KNOWN_HOSTS="$HOME/.sccs/screen_known_hosts"
SSH_BATCH=(
    -o BatchMode=yes
    -o PreferredAuthentications=publickey
    -o IdentitiesOnly=yes
    -o UserKnownHostsFile="${KNOWN_HOSTS}"
    -o StrictHostKeyChecking=accept-new
    -i "${KEY}"
)
if [[ "${SKIP_BLANK}" == "1" ]]; then
    remote_cmd=$(cat <<EOF
set -euo pipefail
SHUTDOWN_SUDOERS='/etc/sudoers.d/sccs-screen-shutdown'
SHUTDOWN_LINE='${SCREEN_USER} ALL=(root) NOPASSWD: /sbin/shutdown -h now, /usr/sbin/shutdown -h now, /sbin/poweroff, /usr/sbin/poweroff'
install -d -m 755 /etc/sudoers.d
printf '%s\n' "\$SHUTDOWN_LINE" > "\$SHUTDOWN_SUDOERS"
chmod 440 "\$SHUTDOWN_SUDOERS"
visudo -cf "\$SHUTDOWN_SUDOERS"
visudo -c
echo "Installed shutdown sudoers"
EOF
)
else
    remote_cmd=$(cat <<EOF
set -euo pipefail
BLANK_PATH='${BLANK_PATH}'
BLANK_SUDOERS='/etc/sudoers.d/sccs-screen-blank'
SHUTDOWN_SUDOERS='/etc/sudoers.d/sccs-screen-shutdown'
BLANK_LINE='${SCREEN_USER} ALL=(root) NOPASSWD: /usr/bin/tee ${BLANK_PATH}'
SHUTDOWN_LINE='${SCREEN_USER} ALL=(root) NOPASSWD: /sbin/shutdown -h now, /usr/sbin/shutdown -h now, /sbin/poweroff, /usr/sbin/poweroff'
install -d -m 755 /etc/sudoers.d
printf '%s\n' "\$BLANK_LINE" > "\$BLANK_SUDOERS"
chmod 440 "\$BLANK_SUDOERS"
visudo -cf "\$BLANK_SUDOERS"
printf '%s\n' "\$SHUTDOWN_LINE" > "\$SHUTDOWN_SUDOERS"
chmod 440 "\$SHUTDOWN_SUDOERS"
visudo -cf "\$SHUTDOWN_SUDOERS"
visudo -c
echo "Installed blank + shutdown sudoers"
EOF
)
fi
printf '%s\n' "$SCREEN_PASS" | ssh "${SSH_BATCH[@]}" "${SCREEN_USER}@${SCREEN_HOST}" \
    "sudo -S -p '' bash -c $(printf '%q' "$remote_cmd")"
if [[ "${SKIP_BLANK}" != "1" ]]; then
    ssh "${SSH_BATCH[@]}" "${SCREEN_USER}@${SCREEN_HOST}" \
        "sudo -n -l | grep -F '/usr/bin/tee ${BLANK_PATH}'" >/dev/null
fi
ssh "${SSH_BATCH[@]}" "${SCREEN_USER}@${SCREEN_HOST}" \
    "sudo -n -l | grep -E 'shutdown -h now|/sbin/poweroff|/usr/sbin/poweroff'" >/dev/null
echo "Done — SCCS can control ${SCREEN_USER}@${SCREEN_HOST} over SSH."
EOS2
        then
            rc=0
        else
            rc=1
        fi
    fi

    if [[ "$rc" -eq 0 ]]; then
        # The panel still holds its old lease until something releases it.
        # Drop that lease, then do every later step (Chromium) on the reserved address.
        if [[ -n "$reserved_ip" && "$reserved_ip" != "$live_host" ]]; then
            info "Releasing the panel DHCP lease so it takes ${reserved_ip}…"
            if ! force_panel_dhcp_renew "$screen_user" "$live_host" "${screen_pass:-}"; then
                warn "Could not start a DHCP renew on ${live_host}"
            fi
            info "Waiting for the panel to answer on ${reserved_ip}…"
            if wait_panel_ssh "$reserved_ip" 60; then
                ok "Panel is at ${reserved_ip}"
                screen_host="$reserved_ip"
            else
                warn "Panel did not appear at ${reserved_ip} after the lease refresh"
                info "The SSH key is installed. Re-run menu 8 when the panel answers on ${reserved_ip}."
                unset screen_pass REPLY_PASS 2>/dev/null || true
                return 1
            fi
        elif [[ -n "$reserved_ip" ]]; then
            screen_host="$reserved_ip"
        fi

        # Ensure ~/.ssh/config HostName for the address SCCS will use long-term.
        if [[ -n "$reserved_ip" ]]; then
            ensure_screen_ssh_config "$screen_user" "$reserved_ip" "$screen_alias" || true
        else
            ensure_screen_ssh_config "$screen_user" "$screen_host" "$screen_alias" || true
        fi

        # Menu 2 runs this helper with sudo -n. Install it now while the
        # panel password (if any) is still available.
        if touchscreen_apt "$screen_user" "$screen_host" "${screen_pass:-}" install; then
            ok "Passwordless apt upgrade is available on ${screen_host}"
        elif [[ -n "${screen_pass:-}" ]]; then
            info "Package update will use the saved panel password on ${screen_host}"
        else
            info "Update will ask once for the panel password, then save it, before apt upgrade on ${screen_host}"
        fi

        if ! apply_touchscreen_desktop_session "$screen_user" "$screen_host" "${screen_pass:-}"; then
            # Browser setup can miss a panel that moved to its reserved address
            # during SSH setup. Login and idle-power were already attempted above.
            if [[ -n "$reserved_ip" && "$reserved_ip" != "$screen_host" ]] \
                && wait_panel_ssh "$reserved_ip" 15 \
                && configure_touchscreen_browser "$screen_user" "$reserved_ip" "${screen_pass:-}"; then
                ok "Chromium homepage + autostart → ${SCCS_UI_URL} (via reserved ${reserved_ip})"
                screen_host="$reserved_ip"
            else
                warn "Chromium homepage was not fully configured on ${screen_host}"
                info "On the panel: install chromium, or re-run menu 8 after the browser is available."
            fi
        fi
    fi

    unset screen_pass REPLY_PASS 2>/dev/null || true
    return "$rc"
}


# ---------------------------------------------------------------------------
# Passwordless desktop login for the panel account.
# Raspberry Pi OS: raspi-config desktop autologin (LightDM) and tty1.
# Armbian: the display manager already in use (LightDM, SDDM, GDM, or greetd).
# The account password is left as it is. Login without it starts on the next boot.
# Args: user host [password]
# Exit 4 when the panel is not Raspberry Pi OS or Armbian.
# ---------------------------------------------------------------------------
configure_touchscreen_autologin() {
    local screen_user="$1" screen_host="$2" screen_pass="${3:-}"

    info "Enabling passwordless desktop login on ${screen_user}@${screen_host}…"

    local rc=0
    if run_user_bash env \
        SCREEN_USER="$screen_user" \
        SCREEN_HOST="$screen_host" \
        SCREEN_PASS="${screen_pass:-}" \
        <<'EOS'
set -euo pipefail
KEY="$HOME/.ssh/sccs_screen"
KNOWN_HOSTS="$HOME/.sccs/screen_known_hosts"
SSH_BATCH=(
    -o BatchMode=yes
    -o PreferredAuthentications=publickey
    -o IdentitiesOnly=yes
    -o UserKnownHostsFile="${KNOWN_HOSTS}"
    -o StrictHostKeyChecking=accept-new
    -o ConnectTimeout=15
    -i "${KEY}"
)
[[ -f "$KEY" ]] || { echo "Missing $KEY" >&2; exit 1; }

remote_cmd=$(cat <<'REMOTE'
set -euo pipefail
USER_NAME=${1:-}
if [[ -z "$USER_NAME" ]]; then
    echo "No panel account name for desktop autologin" >&2
    exit 1
fi
case "$USER_NAME" in
    *[!a-zA-Z0-9._-]*)
        echo "Refusing desktop autologin for unusual account name: ${USER_NAME}" >&2
        exit 1
        ;;
esac
if ! id "$USER_NAME" >/dev/null 2>&1; then
    echo "Panel account ${USER_NAME} does not exist" >&2
    exit 1
fi

family=""
if [[ -f /etc/armbian-release || -f /etc/armbian-image-release ]]; then
    family=armbian
fi
if [[ -r /etc/os-release ]]; then
    os_id=$(. /etc/os-release && printf '%s' "${ID:-}" || true)
    os_like=$(. /etc/os-release && printf '%s' "${ID_LIKE:-}" || true)
    pretty=$(. /etc/os-release && printf '%s' "${PRETTY_NAME:-} ${NAME:-}" || true)
    case "$os_id" in
        armbian) family=armbian ;;
        raspbian|raspberrypi)
            [[ "$family" == armbian ]] || family=rpi
            ;;
    esac
    case " ${os_like} " in
        *" armbian "*) family=armbian ;;
    esac
    if [[ "$family" != armbian && "$pretty" == *[Rr]aspberry[[:space:]][Pp]i* ]]; then
        family=rpi
    fi
fi
if [[ -z "$family" && -f /etc/rpi-issue ]]; then
    family=rpi
fi
if [[ -z "$family" && -x /usr/bin/raspi-config && ! -f /etc/armbian-release && ! -f /etc/armbian-image-release ]]; then
    family=rpi
fi
if [[ -z "$family" ]]; then
    echo "Desktop autologin is only set up for Raspberry Pi OS and Armbian." >&2
    exit 4
fi
echo "Panel OS: ${family}"

session_file_exists() {
    local name="$1"
    [[ -f "/usr/share/wayland-sessions/${name}.desktop" || -f "/usr/share/xsessions/${name}.desktop" ]]
}

pick_session() {
    local state="/var/lib/sddm/state.conf" last="" f
    if [[ -r "$state" ]]; then
        last=$(awk -F= '/^[[:space:]]*Session=/{print substr($0, index($0,"=")+1); exit}' "$state")
        last=${last#"${last%%[![:space:]]*}"}
        last=${last%"${last##*[![:space:]]}"}
        last=${last#\"}
        last=${last%\"}
        last=$(basename "$last" .desktop)
        if [[ -n "$last" ]] && session_file_exists "$last"; then
            printf '%s\n' "$last"
            return 0
        fi
    fi
    local files=(
        /usr/share/wayland-sessions/plasmawayland.desktop
        /usr/share/wayland-sessions/plasma.desktop
        /usr/share/xsessions/plasmax11.desktop
        /usr/share/xsessions/plasma.desktop
        /usr/share/wayland-sessions/xfce.desktop
        /usr/share/xsessions/xfce.desktop
        /usr/share/xsessions/xfce4.desktop
        /usr/share/wayland-sessions/rpd-labwc.desktop
        /usr/share/wayland-sessions/LXDE-pi-labwc.desktop
        /usr/share/xsessions/LXDE-pi.desktop
        /usr/share/xsessions/LXDE-pi-x.desktop
        /usr/share/wayland-sessions/LXDE-pi-wayfire.desktop
    )
    for f in "${files[@]}"; do
        if [[ -f "$f" ]]; then
            basename "$f" .desktop
            return 0
        fi
    done
    local any=()
    shopt -s nullglob
    any=(/usr/share/wayland-sessions/*.desktop /usr/share/xsessions/*.desktop)
    shopt -u nullglob
    if [[ ${#any[@]} -gt 0 ]]; then
        basename "${any[0]}" .desktop
        return 0
    fi
    return 1
}

pick_session_exec() {
    local f exec_line
    local files=(
        /usr/share/wayland-sessions/rpd-labwc.desktop
        /usr/share/wayland-sessions/LXDE-pi-labwc.desktop
        /usr/share/wayland-sessions/labwc.desktop
        /usr/share/wayland-sessions/plasmawayland.desktop
        /usr/share/wayland-sessions/plasma.desktop
        /usr/share/xsessions/plasmax11.desktop
        /usr/share/xsessions/plasma.desktop
        /usr/share/xsessions/xfce.desktop
    )
    for f in "${files[@]}"; do
        [[ -f "$f" ]] || continue
        exec_line=$(awk -F= '/^Exec=/{print substr($0, index($0,"=")+1); exit}' "$f")
        if [[ -n "$exec_line" ]]; then
            printf '%s\n' "$exec_line"
            return 0
        fi
    done
    return 1
}

detect_dm() {
    local target="" base="" unit="" dmfile=""
    if [[ -L /etc/systemd/system/display-manager.service ]]; then
        target=$(readlink -f /etc/systemd/system/display-manager.service || true)
        base=$(basename "$target")
        case "$base" in
            sddm.service) printf '%s\n' sddm; return 0 ;;
            lightdm.service) printf '%s\n' lightdm; return 0 ;;
            gdm.service|gdm3.service) printf '%s\n' gdm; return 0 ;;
            greetd.service) printf '%s\n' greetd; return 0 ;;
        esac
    fi
    if [[ -r /etc/X11/default-display-manager ]]; then
        dmfile=$(tr -d '[:space:]' < /etc/X11/default-display-manager)
        base=$(basename "$dmfile")
        case "$base" in
            sddm|lightdm|greetd) printf '%s\n' "$base"; return 0 ;;
            gdm|gdm3) printf '%s\n' gdm; return 0 ;;
        esac
    fi
    for unit in sddm lightdm gdm3 gdm greetd; do
        if systemctl is-enabled "${unit}.service" >/dev/null 2>&1; then
            case "$unit" in
                gdm3) printf '%s\n' gdm ;;
                *) printf '%s\n' "$unit" ;;
            esac
            return 0
        fi
    done
    if [[ -x /usr/bin/sddm || -x /usr/sbin/sddm || -f /etc/sddm.conf ]]; then
        printf '%s\n' sddm
        return 0
    fi
    if [[ -x /usr/sbin/lightdm || -x /usr/bin/lightdm || -f /etc/lightdm/lightdm.conf ]]; then
        printf '%s\n' lightdm
        return 0
    fi
    if [[ -f /etc/gdm3/custom.conf || -f /etc/gdm3/daemon.conf || -f /etc/gdm/custom.conf ]]; then
        printf '%s\n' gdm
        return 0
    fi
    if [[ -x /usr/sbin/greetd || -f /etc/greetd/config.toml ]]; then
        printf '%s\n' greetd
        return 0
    fi
    return 1
}

ensure_autologin_group() {
    if [[ -f /etc/pam.d/lightdm-autologin ]] && grep -q 'ingroup autologin' /etc/pam.d/lightdm-autologin; then
        getent group autologin >/dev/null 2>&1 || groupadd --system autologin
    fi
    if getent group autologin >/dev/null 2>&1; then
        usermod -aG autologin "$USER_NAME"
        echo "Account ${USER_NAME} is in group autologin"
    fi
    if getent group nopasswdlogin >/dev/null 2>&1; then
        usermod -aG nopasswdlogin "$USER_NAME"
    fi
}

enable_lightdm() {
    local conf="/etc/lightdm/lightdm.conf"
    install -d -m 755 /etc/lightdm/lightdm.conf.d
    if [[ -f "$conf" ]]; then
        if grep -qE '^[[:space:]]*#?[[:space:]]*autologin-user=' "$conf"; then
            sed -i -E "s/^[[:space:]]*#?[[:space:]]*autologin-user=.*/autologin-user=${USER_NAME}/" "$conf"
        fi
        if grep -qE '^[[:space:]]*#?[[:space:]]*autologin-user-timeout=' "$conf"; then
            sed -i -E "s/^[[:space:]]*#?[[:space:]]*autologin-user-timeout=.*/autologin-user-timeout=0/" "$conf"
        fi
    fi
    cat > /etc/lightdm/lightdm.conf.d/zz-sccs-autologin.conf <<EOF
[Seat:*]
autologin-user=${USER_NAME}
autologin-user-timeout=0
EOF
    chmod 644 /etc/lightdm/lightdm.conf.d/zz-sccs-autologin.conf
    ensure_autologin_group
    systemctl enable lightdm.service >/dev/null 2>&1 || true
    systemctl set-default graphical.target
    grep -q "^autologin-user=${USER_NAME}$" /etc/lightdm/lightdm.conf.d/zz-sccs-autologin.conf
    echo "LightDM autologin enabled for ${USER_NAME}"
}

enable_rpi_console_autologin() {
    [[ "$family" == rpi ]] || return 0
    install -d -m 755 /etc/systemd/system/getty@tty1.service.d
    cat > /etc/systemd/system/getty@tty1.service.d/autologin.conf <<EOF
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin ${USER_NAME} --noclear %I \$TERM
EOF
    chmod 644 /etc/systemd/system/getty@tty1.service.d/autologin.conf
    systemctl daemon-reload || true
    echo "Console autologin enabled for ${USER_NAME} on tty1"
}

enable_sddm() {
    local session=""
    session=$(pick_session || true)
    install -d -m 755 /etc/sddm.conf.d
    {
        echo "[Autologin]"
        echo "User=${USER_NAME}"
        if [[ -n "$session" ]]; then
            echo "Session=${session}"
        fi
        echo "Relogin=false"
    } > /etc/sddm.conf.d/zz-sccs-autologin.conf
    chmod 644 /etc/sddm.conf.d/zz-sccs-autologin.conf
    systemctl enable sddm.service >/dev/null 2>&1 || true
    systemctl set-default graphical.target
    grep -q "^User=${USER_NAME}$" /etc/sddm.conf.d/zz-sccs-autologin.conf
    if [[ -n "$session" ]]; then
        echo "SDDM autologin enabled for ${USER_NAME} (session ${session})"
    else
        echo "SDDM autologin enabled for ${USER_NAME} (default session)"
    fi
}

enable_gdm() {
    local conf="" cand
    for cand in /etc/gdm3/custom.conf /etc/gdm3/daemon.conf /etc/gdm/custom.conf; do
        if [[ -f "$cand" ]]; then
            conf=$cand
            break
        fi
    done
    if [[ -z "$conf" ]]; then
        install -d -m 755 /etc/gdm3
        conf=/etc/gdm3/custom.conf
        printf '%s\n' '[daemon]' > "$conf"
    fi
    if grep -qE '^[[:space:]]*#?[[:space:]]*AutomaticLoginEnable[[:space:]]*=' "$conf"; then
        sed -i -E 's/^[[:space:]]*#?[[:space:]]*AutomaticLoginEnable[[:space:]]*=.*/AutomaticLoginEnable=true/' "$conf"
    elif grep -q '^\[daemon\]' "$conf"; then
        sed -i '/^\[daemon\]/a AutomaticLoginEnable=true' "$conf"
    else
        printf '\n[daemon]\nAutomaticLoginEnable=true\n' >> "$conf"
    fi
    if grep -qE '^[[:space:]]*#?[[:space:]]*AutomaticLogin[[:space:]]*=' "$conf"; then
        sed -i -E "s/^[[:space:]]*#?[[:space:]]*AutomaticLogin[[:space:]]*=.*/AutomaticLogin=${USER_NAME}/" "$conf"
    elif grep -q '^\[daemon\]' "$conf"; then
        sed -i "/^\[daemon\]/a AutomaticLogin=${USER_NAME}" "$conf"
    else
        printf 'AutomaticLogin=%s\n' "$USER_NAME" >> "$conf"
    fi
    systemctl enable gdm3.service >/dev/null 2>&1 || systemctl enable gdm.service >/dev/null 2>&1 || true
    systemctl set-default graphical.target
    grep -q "^AutomaticLogin=${USER_NAME}$" "$conf"
    echo "GDM autologin enabled for ${USER_NAME}"
}

enable_greetd() {
    local cfg="/etc/greetd/config.toml" session_cmd="" tmp esc
    [[ -f "$cfg" ]] || { echo "greetd config ${cfg} is missing" >&2; return 1; }
    session_cmd=$(pick_session_exec || true)
    [[ -n "$session_cmd" ]] || session_cmd="labwc"
    esc=$(printf '%s' "$session_cmd" | sed 's/\\/\\\\/g; s/"/\\"/g')
    tmp=$(mktemp)
    awk '
        /^\[initial_session\][[:space:]]*$/ { skip=1; next }
        /^\[/ { skip=0 }
        skip { next }
        { print }
    ' "$cfg" > "$tmp"
    printf '\n[initial_session]\ncommand = "%s"\nuser = "%s"\n' "$esc" "$USER_NAME" >> "$tmp"
    cat "$tmp" > "$cfg"
    rm -f "$tmp"
    chmod 644 "$cfg"
    systemctl enable greetd.service >/dev/null 2>&1 || true
    systemctl set-default graphical.target
    grep -q "^user = \"${USER_NAME}\"$" "$cfg"
    echo "greetd autologin enabled for ${USER_NAME}"
}

if [[ "$family" == rpi ]] && command -v raspi-config >/dev/null 2>&1 \
    && { [[ -e /etc/init.d/lightdm ]] || [[ -x /usr/sbin/lightdm ]] || [[ -f /etc/lightdm/lightdm.conf ]]; }; then
    echo "Raspberry Pi OS: raspi-config desktop autologin"
    if command -v timeout >/dev/null 2>&1; then
        timeout 30 raspi-config nonint do_boot_behaviour B4 \
            || echo "raspi-config did not apply desktop autologin; writing the display-manager config directly"
    else
        raspi-config nonint do_boot_behaviour B4 \
            || echo "raspi-config did not apply desktop autologin; writing the display-manager config directly"
    fi
fi

dm=$(detect_dm || true)
echo "Display manager: ${dm:-none}"
case "$dm" in
    lightdm)
        enable_lightdm
        enable_rpi_console_autologin
        ;;
    sddm) enable_sddm ;;
    gdm) enable_gdm ;;
    greetd) enable_greetd ;;
    *)
        echo "No LightDM, SDDM, GDM, or greetd on this ${family} panel." >&2
        exit 1
        ;;
esac
echo "Passwordless desktop login takes effect on the next boot."
REMOTE
)

rc=0
if [[ -n "${SCREEN_PASS:-}" ]]; then
    printf '%s\n' "$SCREEN_PASS" | ssh "${SSH_BATCH[@]}" "${SCREEN_USER}@${SCREEN_HOST}" \
        "sudo -S -p '' bash -c $(printf '%q' "$remote_cmd") -- $(printf '%q' "$SCREEN_USER")" || rc=$?
else
    ssh "${SSH_BATCH[@]}" "${SCREEN_USER}@${SCREEN_HOST}" \
        "sudo -n bash -c $(printf '%q' "$remote_cmd") -- $(printf '%q' "$SCREEN_USER")" || rc=$?
fi
if [[ "$rc" -eq 0 ]]; then
    exit 0
fi
if [[ "$rc" -eq 4 ]]; then
    exit 4
fi
echo "Desktop autologin failed (exit ${rc})." >&2
exit 1
EOS
    then
        rc=0
    else
        rc=$?
    fi
    return "$rc"
}


# ---------------------------------------------------------------------------
# Stop the panel OS from blanking, dimming, or sleeping the display when idle.
# Raspberry Pi OS: raspi-config screen blanking off, and the labwc swayidle line.
# Armbian: the desktop's own power settings (KDE, XFCE, GNOME, labwc, X11).
# SCCS reed and phase brightness is unchanged.
# Args: user host [password]
# Exit 4 when the panel is not Raspberry Pi OS or Armbian.
# ---------------------------------------------------------------------------
configure_touchscreen_power() {
    local screen_user="$1" screen_host="$2" screen_pass="${3:-}"
    local rc=0

    info "Disabling idle screen power-off on ${screen_user}@${screen_host}…"

    if run_user_bash env \
        SCREEN_USER="$screen_user" \
        SCREEN_HOST="$screen_host" \
        SCREEN_PASS="${screen_pass:-}" \
        <<'EOS'
set -euo pipefail
KEY="$HOME/.ssh/sccs_screen"
KNOWN_HOSTS="$HOME/.sccs/screen_known_hosts"
SSH_BATCH=(
    -o BatchMode=yes
    -o PreferredAuthentications=publickey
    -o IdentitiesOnly=yes
    -o UserKnownHostsFile="${KNOWN_HOSTS}"
    -o StrictHostKeyChecking=accept-new
    -o ConnectTimeout=15
    -i "${KEY}"
)
[[ -f "$KEY" ]] || { echo "Missing $KEY" >&2; exit 1; }

remote_cmd=$(cat <<'REMOTE'
set -euo pipefail
USER_NAME=${1:-}
if [[ -z "$USER_NAME" ]]; then
    echo "No panel account name for power settings" >&2
    exit 1
fi
case "$USER_NAME" in
    *[!a-zA-Z0-9._-]*)
        echo "Refusing power settings for unusual account name: ${USER_NAME}" >&2
        exit 1
        ;;
esac
user_home=$(getent passwd "$USER_NAME" | cut -d: -f6)
if [[ -z "$user_home" || ! -d "$user_home" ]]; then
    echo "Panel account ${USER_NAME} does not exist" >&2
    exit 1
fi
user_group=$(id -gn "$USER_NAME")

family=""
if [[ -f /etc/armbian-release || -f /etc/armbian-image-release ]]; then
    family=armbian
fi
if [[ -r /etc/os-release ]]; then
    os_id=$(. /etc/os-release && printf '%s' "${ID:-}" || true)
    os_like=$(. /etc/os-release && printf '%s' "${ID_LIKE:-}" || true)
    pretty=$(. /etc/os-release && printf '%s' "${PRETTY_NAME:-} ${NAME:-}" || true)
    case "$os_id" in
        armbian) family=armbian ;;
        raspbian|raspberrypi)
            [[ "$family" == armbian ]] || family=rpi
            ;;
    esac
    case " ${os_like} " in
        *" armbian "*) family=armbian ;;
    esac
    if [[ "$family" != armbian && "$pretty" == *[Rr]aspberry[[:space:]][Pp]i* ]]; then
        family=rpi
    fi
fi
if [[ -z "$family" && -f /etc/rpi-issue ]]; then
    family=rpi
fi
if [[ -z "$family" && -x /usr/bin/raspi-config && ! -f /etc/armbian-release && ! -f /etc/armbian-image-release ]]; then
    family=rpi
fi
if [[ -z "$family" ]]; then
    echo "Idle screen power-off is only changed on Raspberry Pi OS and Armbian." >&2
    exit 4
fi
echo "Panel OS: ${family}"

own_user() {
    local path="$1"
    chown "$USER_NAME:$user_group" "$path" 2>/dev/null || chown "$USER_NAME" "$path"
}

kconfig_set() {
    local file="$1" section="$2" key="$3" value="$4"
    install -d -o "$USER_NAME" -g "$user_group" "$(dirname "$file")"
    python3 - "$file" "$section" "$key" "$value" <<'PY'
import os, sys
path, section, key, value = sys.argv[1:5]
lines = []
if os.path.isfile(path):
    with open(path, encoding="utf-8", errors="replace") as fh:
        lines = fh.read().splitlines()
header = section if section.startswith("[") else "[" + section + "]"
out = []
in_section = False
found_section = False
found_key = False
for line in lines:
    stripped = line.strip()
    if stripped.startswith("[") and stripped.endswith("]") and stripped.count("[") >= 1:
        if in_section and not found_key:
            out.append("%s=%s" % (key, value))
            found_key = True
        in_section = stripped == header
        if in_section:
            found_section = True
            found_key = False
        out.append(line)
        continue
    if in_section and "=" in stripped and not stripped.startswith("#"):
        name = stripped.split("=", 1)[0].strip()
        if name == key:
            if not found_key:
                out.append("%s=%s" % (key, value))
                found_key = True
            continue
    out.append(line)
if in_section and not found_key:
    out.append("%s=%s" % (key, value))
if not found_section:
    if out and out[-1] != "":
        out.append("")
    out.append(header)
    out.append("%s=%s" % (key, value))
text = "\n".join(out)
if not text.endswith("\n"):
    text += "\n"
with open(path, "w", encoding="utf-8") as fh:
    fh.write(text)
PY
    own_user "$file"
}

strip_idle_blank() {
    local file="$1" drop_if_empty="${2:-0}" tmp
    [[ -f "$file" ]] || return 0
    tmp=$(mktemp)
    grep -v -E 'swayidle.*(wlopm|dpms|swaylock|loginctl|suspend)' "$file" > "$tmp" || true
    if [[ "$drop_if_empty" == 1 ]] && ! grep -q '[^[:space:]#]' "$tmp"; then
        rm -f "$file" "$tmp"
        echo "Removed ${file} after dropping idle blanking, so the system desktop autostart still runs"
        return 0
    fi
    if cmp -s "$tmp" "$file"; then
        rm -f "$tmp"
        return 0
    fi
    cat "$tmp" > "$file"
    rm -f "$tmp"
    echo "Removed idle screen-blank lines from ${file}"
}

append_consoleblank() {
    local file="$1" real edited="" 
    [[ -f "$file" ]] || return 0
    real=$(readlink -f "$file")
    case " ${CONSOLEBLANK_DONE:-} " in
        *" ${real} "*) return 0 ;;
    esac
    CONSOLEBLANK_DONE="${CONSOLEBLANK_DONE:-} ${real}"
    if grep -q 'consoleblank=' "$file"; then
        sed -i -E 's/consoleblank=[0-9]+/consoleblank=0/g' "$file"
    elif [[ -s "$file" ]]; then
        sed -i 's/[[:space:]]*$/ consoleblank=0/' "$file"
    fi
    echo "Console blanking off in ${file}"
}

if [[ "$family" == rpi ]] && command -v raspi-config >/dev/null 2>&1; then
    echo "Raspberry Pi OS: disable screen blanking"
    if command -v timeout >/dev/null 2>&1; then
        SUDO_USER="$USER_NAME" timeout 30 raspi-config nonint do_blanking 1 \
            || echo "raspi-config did not disable screen blanking; continuing with direct settings"
    else
        SUDO_USER="$USER_NAME" raspi-config nonint do_blanking 1 \
            || echo "raspi-config did not disable screen blanking; continuing with direct settings"
    fi
fi

strip_idle_blank "$user_home/.config/labwc/autostart" 1
strip_idle_blank /etc/xdg/labwc/autostart 0

if [[ -f "$user_home/.config/wayfire.ini" ]]; then
    kconfig_set "$user_home/.config/wayfire.ini" idle dpms_timeout 0
    kconfig_set "$user_home/.config/wayfire.ini" idle screensaver_timeout 0
    echo "Wayfire idle blanking disabled"
fi

install -d -m 755 /etc/X11/xorg.conf.d
cat > /etc/X11/xorg.conf.d/10-sccs-no-blank.conf <<'XORG'
Section "ServerFlags"
    Option "BlankTime" "0"
    Option "StandbyTime" "0"
    Option "SuspendTime" "0"
    Option "OffTime" "0"
EndSection
Section "Extensions"
    Option "DPMS" "Disable"
EndSection
XORG
chmod 644 /etc/X11/xorg.conf.d/10-sccs-no-blank.conf
echo "X11 DPMS and blank timers disabled"

lx_file=""
for lx_file in \
    /etc/xdg/lxsession/LXDE-pi/autostart \
    /etc/xdg/lxsession/LXDE/autostart \
    "$user_home/.config/lxsession/LXDE-pi/autostart" \
    "$user_home/.config/lxsession/LXDE/autostart"
do
    [[ -f "$lx_file" ]] || continue
    for lx_line in "@xset s off" "@xset s noblank" "@xset -dpms"; do
        grep -qxF "$lx_line" "$lx_file" || printf '%s\n' "$lx_line" >> "$lx_file"
    done
    echo "X11 blanking disabled in ${lx_file}"
done

install -d -o "$USER_NAME" -g "$user_group" "$user_home/.config/autostart"
cat > "$user_home/.config/autostart/sccs-no-dpms.desktop" <<'DESK'
[Desktop Entry]
Type=Application
Version=1.0
Name=SCCS keep display on
Comment=Stop the X server from blanking the panel
Exec=sh -c "xset s off; xset s noblank; xset -dpms"
Terminal=false
OnlyShowIn=XFCE;LXDE;X-Cinnamon;MATE;
X-GNOME-Autostart-enabled=true
StartupNotify=false
DESK
own_user "$user_home/.config/autostart/sccs-no-dpms.desktop"

if [[ -f /etc/xdg/autostart/xscreensaver.desktop || -x /usr/bin/xscreensaver ]]; then
    cat > "$user_home/.config/autostart/xscreensaver.desktop" <<'DESK'
[Desktop Entry]
Type=Application
Name=XScreenSaver
Hidden=true
DESK
    own_user "$user_home/.config/autostart/xscreensaver.desktop"
    echo "xscreensaver autostart hidden"
fi

# Armbian KDE often has the session desktop file even when a non-interactive
# SSH PATH does not include plasmashell. Match the install, not the PATH.
if [[ -x /usr/bin/plasmashell || -x /usr/bin/startplasma-wayland || -x /usr/bin/startplasma-x11 \
    || -x /usr/bin/kwriteconfig6 || -x /usr/bin/kwriteconfig5 \
    || -x /usr/bin/kded6 || -x /usr/bin/kded5 \
    || -f /usr/share/wayland-sessions/plasma.desktop \
    || -f /usr/share/wayland-sessions/plasmawayland.desktop \
    || -f /usr/share/xsessions/plasma.desktop \
    || -f /usr/share/xsessions/plasmax11.desktop \
    || -d /usr/share/plasma \
    || -f "$user_home/.config/powerdevilrc" \
    || -f "$user_home/.config/powermanagementprofilesrc" ]]; then
    profile=""
    for profile in AC Battery LowBattery; do
        kconfig_set "$user_home/.config/powerdevilrc" "[${profile}][Display]" DimDisplayWhenIdle false
        kconfig_set "$user_home/.config/powerdevilrc" "[${profile}][Display]" DimDisplayIdleTimeoutSec -1
        kconfig_set "$user_home/.config/powerdevilrc" "[${profile}][Display]" TurnOffDisplayWhenIdle false
        kconfig_set "$user_home/.config/powerdevilrc" "[${profile}][Display]" TurnOffDisplayIdleTimeoutSec -1
        kconfig_set "$user_home/.config/powerdevilrc" "[${profile}][Display]" TurnOffDisplayIdleTimeoutWhenLockedSec -1
        kconfig_set "$user_home/.config/powerdevilrc" "[${profile}][Display]" LockBeforeTurnOffDisplay false
        kconfig_set "$user_home/.config/powerdevilrc" "[${profile}][SuspendAndShutdown]" AutoSuspendAction 0
        kconfig_set "$user_home/.config/powerdevilrc" "[${profile}][SuspendAndShutdown]" AutoSuspendIdleTimeoutSec -1
        kconfig_set "$user_home/.config/powermanagementprofilesrc" "[${profile}][DPMSControl]" idleTime 0
        kconfig_set "$user_home/.config/powermanagementprofilesrc" "[${profile}][DimDisplay]" idleTime 0
        kconfig_set "$user_home/.config/powermanagementprofilesrc" "[${profile}][SuspendSession]" idleTime 0
    done
    kconfig_set "$user_home/.config/kscreenlockerrc" "[Daemon]" Autolock false
    kconfig_set "$user_home/.config/kscreenlockerrc" "[Daemon]" LockOnResume false
    echo "KDE idle dim, screen-off, sleep, and autolock disabled"
fi

if command -v xfce4-power-manager >/dev/null 2>&1 \
    || command -v xfce4-session >/dev/null 2>&1 \
    || [[ -d "$user_home/.config/xfce4" ]]; then
    install -d -o "$USER_NAME" -g "$user_group" \
        "$user_home/.config/xfce4/xfconf/xfce-perchannel-xml"
    python3 - "$user_home" <<'PY'
import os, sys
import xml.etree.ElementTree as ET
home = sys.argv[1]
base = os.path.join(home, ".config/xfce4/xfconf/xfce-perchannel-xml")

def update(path, channel, props):
    if os.path.isfile(path):
        tree = ET.parse(path)
        root = tree.getroot()
    else:
        root = ET.Element("channel", {"name": channel, "version": "1.0"})
        tree = ET.ElementTree(root)
    for name, typ, value in props:
        el = None
        for child in list(root):
            if child.tag == "property" and child.get("name") == name:
                el = child
                break
        if el is None:
            el = ET.SubElement(root, "property")
        el.set("name", name)
        el.set("type", typ)
        el.set("value", value)
    tree.write(path, encoding="utf-8", xml_declaration=True)

update(os.path.join(base, "xfce4-power-manager.xml"), "xfce4-power-manager", [
    ("blank-on-ac", "int", "0"),
    ("blank-on-battery", "int", "0"),
    ("dpms-enabled", "bool", "false"),
    ("dpms-on-ac-off", "int", "0"),
    ("dpms-on-ac-sleep", "int", "0"),
    ("dpms-on-battery-off", "int", "0"),
    ("dpms-on-battery-sleep", "int", "0"),
    ("inactivity-on-ac", "int", "0"),
    ("inactivity-on-battery", "int", "0"),
    ("lock-screen-suspend-hibernate", "bool", "false"),
    ("presentation-mode", "bool", "false"),
])
saver_path = os.path.join(base, "xfce4-screensaver.xml")
if os.path.isfile(saver_path):
    tree = ET.parse(saver_path)
    root = tree.getroot()
else:
    root = ET.Element("channel", {"name": "xfce4-screensaver", "version": "1.0"})
    tree = ET.ElementTree(root)
for child in list(root):
    if child.tag == "property" and child.get("name") in ("saver", "lock", "enabled", "lock/enabled"):
        root.remove(child)
saver = ET.SubElement(root, "property", {"name": "saver", "type": "empty"})
ET.SubElement(saver, "property", {"name": "enabled", "type": "bool", "value": "false"})
lock = ET.SubElement(root, "property", {"name": "lock", "type": "empty"})
ET.SubElement(lock, "property", {"name": "enabled", "type": "bool", "value": "false"})
tree.write(saver_path, encoding="utf-8", xml_declaration=True)
PY
    own_user "$user_home/.config/xfce4/xfconf/xfce-perchannel-xml/xfce4-power-manager.xml"
    own_user "$user_home/.config/xfce4/xfconf/xfce-perchannel-xml/xfce4-screensaver.xml"
    echo "XFCE blanking, sleep, and screensaver disabled"
fi

if command -v lxqt-powermanagement >/dev/null 2>&1 || [[ -d "$user_home/.config/lxqt" ]]; then
    kconfig_set "$user_home/.config/lxqt/lxqt-powermanagement.conf" "[General]" enableIdlenessWatcher false
    kconfig_set "$user_home/.config/lxqt/lxqt-powermanagement.conf" "[General]" enableBatteryWatcher false
    echo "LXQt idle power actions disabled"
fi

if command -v gsettings >/dev/null 2>&1 && command -v dbus-run-session >/dev/null 2>&1 \
    && command -v runuser >/dev/null 2>&1; then
    schemas=$(runuser -u "$USER_NAME" -- dbus-run-session -- gsettings list-schemas 2>/dev/null || true)
    gset() {
        local schema="$1" key="$2" value="$3"
        printf '%s\n' "$schemas" | grep -qx "$schema" || return 0
        runuser -u "$USER_NAME" -- dbus-run-session -- gsettings set "$schema" "$key" "$value" \
            || echo "Could not set ${schema} ${key}"
    }
    gset org.gnome.desktop.session idle-delay 0
    gset org.gnome.desktop.screensaver lock-enabled false
    gset org.gnome.desktop.screensaver idle-activation-enabled false
    gset org.gnome.settings-daemon.plugins.power sleep-inactive-ac-type "'nothing'"
    gset org.gnome.settings-daemon.plugins.power sleep-inactive-ac-timeout 0
    gset org.gnome.settings-daemon.plugins.power sleep-inactive-battery-type "'nothing'"
    gset org.gnome.settings-daemon.plugins.power sleep-inactive-battery-timeout 0
    gset org.gnome.settings-daemon.plugins.power idle-dim false
    gset org.cinnamon.desktop.session idle-delay 0
    gset org.cinnamon.desktop.screensaver lock-enabled false
    gset org.cinnamon.settings-daemon.plugins.power sleep-inactive-ac-type "'nothing'"
    gset org.cinnamon.settings-daemon.plugins.power sleep-inactive-ac-timeout 0
    gset org.mate.power-manager sleep-display-ac 0
    gset org.mate.power-manager sleep-display-battery 0
    gset org.mate.power-manager idle-dim-ac false
    gset org.mate.screensaver idle-activation-enabled false
    gset org.mate.screensaver lock-enabled false
    echo "GNOME, Cinnamon, and MATE idle settings updated where those schemas exist"
fi

install -d -m 755 /etc/systemd/logind.conf.d
cat > /etc/systemd/logind.conf.d/sccs-no-idle.conf <<'LOGIN'
[Login]
IdleAction=ignore
LOGIN
chmod 644 /etc/systemd/logind.conf.d/sccs-no-idle.conf
echo "logind will not sleep the panel when it is idle"

CONSOLEBLANK_DONE=""
append_consoleblank /boot/firmware/cmdline.txt
append_consoleblank /boot/cmdline.txt
if [[ -f /boot/armbianEnv.txt ]]; then
    if grep -q 'consoleblank=' /boot/armbianEnv.txt; then
        sed -i -E 's/consoleblank=[0-9]+/consoleblank=0/g' /boot/armbianEnv.txt
    elif grep -q '^extraargs=' /boot/armbianEnv.txt; then
        sed -i 's/^extraargs=/extraargs=consoleblank=0 /' /boot/armbianEnv.txt
    else
        printf '\nextraargs=consoleblank=0\n' >> /boot/armbianEnv.txt
    fi
    echo "Console blanking off in /boot/armbianEnv.txt"
fi
if [[ -f /etc/kbd/config ]]; then
    if grep -q '^BLANK_TIME=' /etc/kbd/config; then
        sed -i -E 's/^BLANK_TIME=.*/BLANK_TIME=0/' /etc/kbd/config
    else
        printf '\nBLANK_TIME=0\n' >> /etc/kbd/config
    fi
    if grep -q '^POWERDOWN_TIME=' /etc/kbd/config; then
        sed -i -E 's/^POWERDOWN_TIME=.*/POWERDOWN_TIME=0/' /etc/kbd/config
    else
        printf '\nPOWERDOWN_TIME=0\n' >> /etc/kbd/config
    fi
    echo "Console blank timers cleared in /etc/kbd/config"
fi

echo "Idle screen power-off is disabled. It takes effect on the next boot."
REMOTE
)

rc=0
if [[ -n "${SCREEN_PASS:-}" ]]; then
    printf '%s\n' "$SCREEN_PASS" | ssh "${SSH_BATCH[@]}" "${SCREEN_USER}@${SCREEN_HOST}" \
        "sudo -S -p '' bash -c $(printf '%q' "$remote_cmd") -- $(printf '%q' "$SCREEN_USER")" || rc=$?
else
    ssh "${SSH_BATCH[@]}" "${SCREEN_USER}@${SCREEN_HOST}" \
        "sudo -n bash -c $(printf '%q' "$remote_cmd") -- $(printf '%q' "$SCREEN_USER")" || rc=$?
fi
if [[ "$rc" -eq 0 ]]; then
    exit 0
fi
if [[ "$rc" -eq 4 ]]; then
    exit 4
fi
echo "Idle screen power-off setup failed (exit ${rc})." >&2
exit 1
EOS
    then
        rc=0
    else
        rc=$?
    fi
    return "$rc"
}


# ---------------------------------------------------------------------------
# Chromium homepage + graphical autostart on a touchscreen panel.
# Sets homepage to the control Pi UI and launches Chromium when the desktop
# starts (labwc / Raspberry Pi OS runs lxsession-xdg-autostart).
# Args: user host [password]
# Password optional — used only for system Chromium policy install (sudo).
# ---------------------------------------------------------------------------
configure_touchscreen_browser() {
    local screen_user="$1" screen_host="$2" screen_pass="${3:-}"
    local ui_url="${SCCS_UI_URL:-http://${LAN_ADDR}/}"
    # Normalise: ensure scheme + trailing slash for bare IPs
    case "$ui_url" in
        http://*|https://*) ;;
        *) ui_url="http://${ui_url}" ;;
    esac
    [[ "$ui_url" == */ ]] || ui_url="${ui_url}/"

    info "Configuring Chromium on ${screen_user}@${screen_host} → ${ui_url}"

    if run_user_bash env \
        SCREEN_USER="$screen_user" \
        SCREEN_HOST="$screen_host" \
        SCREEN_PASS="${screen_pass:-}" \
        SCCS_UI_URL="$ui_url" \
        <<'EOS'
set -euo pipefail
KEY="$HOME/.ssh/sccs_screen"
KNOWN_HOSTS="$HOME/.sccs/screen_known_hosts"
SSH_BATCH=(
    -o BatchMode=yes
    -o PreferredAuthentications=publickey
    -o IdentitiesOnly=yes
    -o UserKnownHostsFile="${KNOWN_HOSTS}"
    -o StrictHostKeyChecking=accept-new
    -o ConnectTimeout=15
    -i "${KEY}"
)

[[ -f "$KEY" ]] || { echo "Missing $KEY" >&2; exit 1; }

# --- user-level: XDG autostart + seed profile homepage (no sudo) ---
# Do NOT write ~/.config/labwc/autostart — a user file replaces the system
# session autostart (panel, pcmanfm, lxsession-xdg-autostart).
remote_user=$(cat <<EOF
set -euo pipefail
UI_URL=$(printf '%q' "$SCCS_UI_URL")

BROWSER=""
for c in chromium chromium-browser google-chrome; do
    if command -v "\$c" >/dev/null 2>&1; then
        BROWSER=\$(command -v "\$c")
        break
    fi
done
if [[ -z "\$BROWSER" ]]; then
    for p in /usr/bin/chromium /usr/bin/chromium-browser /usr/bin/google-chrome; do
        if [[ -x "\$p" ]]; then BROWSER="\$p"; break; fi
    done
fi
if [[ -z "\$BROWSER" ]]; then
    echo "Chromium not installed on this panel" >&2
    exit 3
fi
echo "Browser: \$BROWSER"

mkdir -p "\$HOME/.config/autostart"
DESKTOP="\$HOME/.config/autostart/sccs-ui.desktop"
# 10" panels are 1280×800. Scale factor 1 keeps one CSS pixel per device
# pixel; a compositor scale of 1.5 or 2 makes Chromium fill a larger buffer.
# KDE session restore launches the stock Chromium desktop file first. A later
# autostart then hits "Opening in existing browser session" and exits, so
# these flags never apply. Kill that restored browser before starting the
# real one. --disable-gpu --disable-gpu-compositing paints the Neumorphism
# halfway highlight as a few flat bands, so the EGL launch flags stay.
# Expand BROWSER/UI_URL when writing so the .desktop has concrete paths.
cat > "\$DESKTOP" <<DESK
[Desktop Entry]
Type=Application
Version=1.0
Name=SCCS Control UI
Comment=Open the camper control system UI on boot
Exec=sh -c "sleep 4; killall -q chromium || true; sleep 1; exec \$BROWSER --noerrdialogs --disable-session-crashed-bubble --disable-infobars --check-for-update-interval=31536000 --disable-features=TranslateUI --enable-gpu-rasterization --ignore-gpu-blocklist --enable-zero-copy --use-gl=egl --force-device-scale-factor=1 --homepage=\$UI_URL \$UI_URL"
Terminal=false
X-GNOME-Autostart-enabled=true
StartupNotify=false
Categories=Network;
DESK
chmod 644 "\$DESKTOP"
echo "Wrote \$DESKTOP"

# Seed Chromium profile so first launch uses the control Pi as homepage/startup.
PREF_DIR="\$HOME/.config/chromium/Default"
mkdir -p "\$PREF_DIR"
# Filename is literally "First Run" — skips the welcome wizard
touch "\$HOME/.config/chromium/First Run" 2>/dev/null || true
if [[ ! -f "\$PREF_DIR/Preferences" ]]; then
    cat > "\$PREF_DIR/Preferences" <<PREF
{
  "browser": {
    "custom_chrome_frame": false,
    "has_seen_welcome_page": true
  },
  "distribution": {
    "import_bookmarks": false,
    "make_chrome_default": false,
    "skip_first_run_ui": true
  },
  "homepage": "\$UI_URL",
  "homepage_is_newtabpage": false,
  "session": {
    "restore_on_startup": 4,
    "startup_urls": ["\$UI_URL"]
  }
}
PREF
    echo "Seeded Chromium Preferences homepage"
else
    if command -v python3 >/dev/null 2>&1; then
        python3 - "\$PREF_DIR/Preferences" "\$UI_URL" <<'PY'
import json, sys
path, url = sys.argv[1], sys.argv[2]
try:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
except Exception:
    sys.exit(0)
data["homepage"] = url
data["homepage_is_newtabpage"] = False
sess = data.setdefault("session", {})
sess["restore_on_startup"] = 4
sess["startup_urls"] = [url]
browser = data.setdefault("browser", {})
browser["has_seen_welcome_page"] = True
with open(path, "w", encoding="utf-8") as f:
    json.dump(data, f, separators=(",", ":"))
print("Patched existing Chromium Preferences")
PY
    fi
fi

# Verify GPU acceleration is actually active rather than a silent
# SwiftShader/llvmpipe software fallback (Chromium's GPU allowlist can
# reject unrecognized Mesa/V3D/Panfrost driver strings without erroring).
# Uses a throwaway profile so it doesn't collide with the kiosk instance.
GPU_TMP="\$(mktemp -d)"
if "\$BROWSER" --headless=new --disable-gpu-sandbox --enable-gpu-rasterization \
    --ignore-gpu-blocklist --use-gl=egl --user-data-dir="\$GPU_TMP" \
    --dump-dom chrome://gpu >"\$GPU_TMP/gpu.html" 2>/dev/null; then
    if grep -qiE "swiftshader|llvmpipe" "\$GPU_TMP/gpu.html"; then
        echo "GPU: WARNING - Chromium is using software rendering (SwiftShader/llvmpipe); check chrome://gpu on the panel"
    elif grep -qi "hardware accelerated" "\$GPU_TMP/gpu.html"; then
        echo "GPU: hardware acceleration confirmed"
    else
        echo "GPU: status unclear - check chrome://gpu on the panel manually"
    fi
else
    echo "GPU: could not run headless probe - check chrome://gpu on the panel manually"
fi
rm -rf "\$GPU_TMP"

echo "User-level Chromium autostart ready → \$UI_URL"
EOF
)

if ! ssh "${SSH_BATCH[@]}" "${SCREEN_USER}@${SCREEN_HOST}" \
    "bash -c $(printf '%q' "$remote_user")"; then
    rc=$?
    echo "User-level Chromium setup failed (exit $rc)" >&2
    exit "$rc"
fi

# --- system policy (sudo): homepage survives profile resets ---
policy_cmd=$(cat <<EOF
set -euo pipefail
UI_URL=$(printf '%q' "$SCCS_UI_URL")
for base in /etc/chromium /etc/chromium-browser; do
    install -d -m 755 "\$base/policies/managed"
    cat > "\$base/policies/managed/sccs-touchscreen.json" <<POL
{
  "HomepageLocation": "\$UI_URL",
  "HomepageIsNewTabPage": false,
  "ShowHomeButton": true,
  "RestoreOnStartup": 4,
  "RestoreOnStartupURLs": ["\$UI_URL"]
}
POL
    chmod 644 "\$base/policies/managed/sccs-touchscreen.json"
    echo "Installed \$base/policies/managed/sccs-touchscreen.json"
done
EOF
)

echo "Installing Chromium managed policy (homepage)…"
if [[ -n "${SCREEN_PASS:-}" ]]; then
    if ! printf '%s\n' "$SCREEN_PASS" | ssh "${SSH_BATCH[@]}" "${SCREEN_USER}@${SCREEN_HOST}" \
        "sudo -S -p '' bash -c $(printf '%q' "$policy_cmd")"; then
        echo "Policy install failed (sudo) — homepage still set via autostart URL + profile" >&2
        exit 0
    fi
else
    if ! ssh "${SSH_BATCH[@]}" "${SCREEN_USER}@${SCREEN_HOST}" \
        "sudo -n bash -c $(printf '%q' "$policy_cmd")" 2>/dev/null; then
        echo "No passwordless sudo for policy install — homepage still set via autostart URL + profile"
    fi
fi

echo "Done — Chromium opens ${SCCS_UI_URL} on graphical login."
EOS
    then
        return 0
    else
        return 1
    fi
}


# Desktop login, idle-power off, and Chromium homepage for one panel.
# Returns 1 only when Chromium setup fails. Login and power print their own result.
# Args: user host [password]
apply_touchscreen_desktop_session() {
    local screen_user="$1" screen_host="$2" screen_pass="${3:-}"
    local auto_rc=0 power_rc=0

    configure_touchscreen_autologin "$screen_user" "$screen_host" "${screen_pass:-}" || auto_rc=$?
    if [[ "$auto_rc" -eq 0 ]]; then
        ok "Passwordless desktop login for ${screen_user} on ${screen_host} (next boot)"
    elif [[ "$auto_rc" -eq 4 ]]; then
        info "Desktop autologin skipped on ${screen_host} — not Raspberry Pi OS or Armbian"
    else
        warn "Passwordless desktop login was not enabled on ${screen_host}"
        info "On the panel, turn on automatic desktop login for ${screen_user}. It applies on the next boot."
    fi

    configure_touchscreen_power "$screen_user" "$screen_host" "${screen_pass:-}" || power_rc=$?
    if [[ "$power_rc" -eq 0 ]]; then
        ok "Idle screen power-off disabled on ${screen_host} (next boot)"
    elif [[ "$power_rc" -eq 4 ]]; then
        info "Idle screen power-off left unchanged on ${screen_host} — not Raspberry Pi OS or Armbian"
    else
        warn "Idle screen power-off was not disabled on ${screen_host}"
    fi

    if configure_touchscreen_browser "$screen_user" "$screen_host" "${screen_pass:-}"; then
        ok "Chromium homepage + autostart → ${SCCS_UI_URL}"
        return 0
    fi
    return 1
}


# Apply desktop login, Chromium, and idle-power settings to every [screens] panel.
# Unreachable panels are skipped. Uses the password saved at setup when sudo needs it.
apply_all_configured_touchscreen_desktop() {
    local listing="" line name host user _blank _mac friendly label pass=""
    local -a rows=()

    [[ -f "${CONF:-}" ]] || return 0
    listing="$(list_configured_screens 2>/dev/null || true)"
    [[ -n "$listing" ]] || return 0
    mapfile -t rows < <(printf '%s\n' "$listing")
    [[ ${#rows[@]} -gt 0 ]] || return 0

    if [[ ${#rows[@]} -eq 1 ]]; then
        info "Applying desktop login, Chromium, and idle-power settings on 1 configured touchscreen…"
    else
        info "Applying desktop login, Chromium, and idle-power settings on ${#rows[@]} configured touchscreens…"
    fi

    for line in "${rows[@]}"; do
        [[ -n "$line" ]] || continue
        IFS=$'\t' read -r name host user _blank _mac friendly <<<"$line"
        label="${friendly:-$name}"
        [[ -n "$label" ]] || label="$host"
        if [[ ! "$user" =~ ^[A-Za-z_][-A-Za-z0-9_]*$ ]] \
            || [[ ! "$host" =~ ^[A-Za-z0-9][-A-Za-z0-9._]*$ ]]; then
            warn "${label}: skipped desktop settings — unexpected host or username in [screens]"
            continue
        fi
        if ! panel_ssh_port_open "$host" 3; then
            warn "${label} (${host}) is not reachable — skipped desktop settings"
            continue
        fi
        echo
        section_title "${label}"
        pass=""
        if load_screen_password "$name" "$host" "$user"; then
            pass="$REPLY_PASS"
            info "Using the saved panel password for ${label}"
        fi
        REPLY_PASS=""
        apply_touchscreen_desktop_session "$user" "$host" "$pass" || true
        pass=""
    done
    unset pass REPLY_PASS 2>/dev/null || true
}


# Resolve which ethernet iface should be the van LAN (default eth0).
# If LAN_ADDR is already assigned, prefer that device (USB adapters often show as eth1).
# Never choose a phone USB tether, even when it currently holds LAN_ADDR.
resolve_lan_if() {
    local preferred="${LAN_IF:-eth0}" found="" dev
    found="$(ip -4 -o addr show 2>/dev/null \
        | awk -v a="${LAN_ADDR:-10.10.10.1}/" '$0 ~ a {print $2; exit}')"
    if [[ -n "$found" ]] && ! is_phone_uplink_if "$found"; then
        printf '%s' "$found"
        return 0
    fi
    if ip link show "$preferred" &>/dev/null && ! is_phone_uplink_if "$preferred"; then
        printf '%s' "$preferred"
        return 0
    fi
    for dev in eth0 eth1 enx0 enp1s0; do
        if ip link show "$dev" &>/dev/null && ! is_phone_uplink_if "$dev"; then
            printf '%s' "$dev"
            return 0
        fi
    done
    return 1
}

# mode: optional | required
step_lan() {
    local mode="${1:-optional}"
    local lan_if
    step_begin "LAN gateway · Pi-hole DHCP/DNS · NAT"
    require_checkout
    if ! lan_if="$(resolve_lan_if)"; then
        skip_note "LAN skipped — no ethernet interface found"
        return 0
    fi
    if ! ip link show wlan0 &>/dev/null && [[ -z "${WAN_IF:-}" && -z "${WAN_IFS:-}" ]]; then
        skip_note "LAN skipped — no wlan0 (connect Wi‑Fi first, or set WAN_IF)"
        return 0
    fi

    if [[ "$mode" == "optional" ]]; then
        ask_yn "Configure ${lan_if}=${LAN_ADDR}, NAT via ${WAN_IF:-wlan0}, and Pi-hole (DNS + DHCP)?" y
        [[ "$REPLY" == "y" ]] || { skip_note "LAN gateway / Pi-hole skipped"; return 0; }
    fi

    LAN_IF="$lan_if" \
    LAN_ADDR="$LAN_ADDR" \
    DHCP_RANGE_START="${DHCP_RANGE_START:-10.10.10.50}" \
    DHCP_RANGE_END="${DHCP_RANGE_END:-10.10.10.200}" \
    SCCS_CONF="$CONF" \
    WAN_IF="${WAN_IF:-wlan0}" \
        apply_lan_nat_dhcp
    ok "Network gateway configured"
}

# Candidate USB tether / RNDIS / phone-as-modem style interfaces (not eth0 LAN).
list_tether_candidates() {
    local dev
    for dev in $(ls /sys/class/net 2>/dev/null | sort); do
        [[ "$dev" == "lo" || "$dev" == "eth0" || "$dev" == "wlan0" || "$dev" == "wlan1" ]] && continue
        # Common: eth1, usb0, enx..., rndis0, enp*s*u*
        if [[ "$dev" =~ ^(eth[1-9]|usb[0-9]|rndis[0-9]|enx[0-9a-f]+|enp[0-9]+s[0-9]+u[0-9]+) ]]; then
            echo "$dev"
        elif [[ -d "/sys/class/net/$dev/device" ]]; then
            # Any non-wlan extra interface with a device (likely USB)
            if ! [[ "$dev" =~ ^wlan ]]; then
                # Skip pure virtual bridges/veth unless they look like usb
                if [[ "$dev" =~ ^(veth|br|docker|virbr) ]]; then
                    continue
                fi
                # Include other eth* beyond eth0
                if [[ "$dev" =~ ^eth ]]; then
                    echo "$dev"
                fi
            fi
        fi
    done
}

nm_con_for_device() {
    local dev="$1"
    nmcli -t -f NAME,DEVICE connection show | awk -F: -v d="$dev" '$2==d {print $1; exit}'
}

# Guided phone USB tethering: detect interface, keep Wi-Fi preferred, refresh NAT.
# mode: optional | required
step_usb_tether() {
    local mode="${1:-optional}"
    step_begin "Configure iPhone USB Hotspot"
    require_checkout
    if [[ "$mode" == "optional" ]]; then
        ask_yn "Set up phone USB tethering as internet uplink now?" y
        if [[ "$REPLY" != "y" ]]; then
            skip_note "USB tether skipped — use menu later when a phone is available"
            return 0
        fi
    fi

    echo
    info "This shares the phone’s internet to the van LAN (via the Pi)."
    info "Works with iPhone USB hotspot, Android USB tethering, etc."
    echo
    echo "  ${C_BOLD}On the phone:${C_RESET}"
    echo "    1. Unlock the phone"
    echo "    2. Enable ${C_BOLD}USB tethering${C_RESET} / ${C_BOLD}Personal Hotspot${C_RESET} (USB, not only Wi‑Fi)"
    echo "    3. Plug the phone into a ${C_BOLD}USB port on the Pi${C_RESET}"
    echo "    4. If prompted on the phone, allow the computer / trust this computer"
    echo
    read -r -p "  Press Enter when the phone is plugged in and tethering is on (s = skip)… " ans || true
    if [[ "${ans,,}" == "s" || "${ans,,}" == "skip" ]]; then
        skip_note "USB tether skipped by user"
        return 0
    fi

    local before="" after="" found="" tries=0 max_tries=45
    before="$(list_tether_candidates | tr '\n' ' ')"
    info "Interfaces already looking like tether candidates: ${before:-none}"
    info "Scanning for a phone USB network device…"
    echo "  ${C_DIM}(type s + Enter anytime to cancel)${C_RESET}"

    while (( tries < max_tries )); do
        mapfile -t cands < <(list_tether_candidates)
        if [[ ${#cands[@]} -ge 1 ]]; then
            # Prefer a newly appeared interface if we can tell
            found=""
            for d in "${cands[@]}"; do
                if [[ " $before " != *" $d "* ]]; then
                    found="$d"
                    break
                fi
            done
            [[ -z "$found" ]] && found="${cands[0]}"
            ok "Found candidate interface: ${C_BOLD}${found}${C_RESET}"
            break
        fi
        tries=$((tries + 1))
        if (( tries % 5 == 1 )); then
            info "Scan ${tries}/${max_tries}: no tether device yet…"
            info "  nmcli device status:"
            nmcli -t -f DEVICE,TYPE,STATE,CONNECTION device status 2>/dev/null | sed 's/^/    /' || true
            info "  links:"
            ip -br link 2>/dev/null | sed 's/^/    /' || true
        fi
        ans=""
        if read -r -t 2 ans 2>/dev/null; then
            case "${ans,,}" in
                s|skip|q) skip_note "USB tether scan cancelled"; return 0 ;;
            esac
        fi
    done

    if [[ -z "$found" ]]; then
        warn "No USB tether interface appeared."
        info "Check cable, phone tethering setting, and try another USB port."
        ask_yn "Enter an interface name manually (e.g. eth1)?" n
        if [[ "$REPLY" == "y" ]]; then
            read -r -p "  Interface name: " found || true
            found="$(echo "$found" | tr -d '[:space:]')"
            if [[ -z "$found" ]] || ! ip link show "$found" &>/dev/null; then
                skip_note "USB tether aborted — invalid interface"
                return 0
            fi
        else
            skip_note "USB tether not configured — no interface found"
            return 0
        fi
    fi

    echo
    info "Device detail for ${found}:"
    ip -br link show "$found" 2>/dev/null | sed 's/^/    /' || true
    ip -4 -br addr show "$found" 2>/dev/null | sed 's/^/    /' || true
    nmcli device show "$found" 2>/dev/null | grep -E 'GENERAL\.(DEVICE|TYPE|STATE|CONNECTION)|IP4\.(ADDRESS|GATEWAY)' | sed 's/^/    /' || true

    # Bring interface up via NetworkManager
    info "Connecting ${found} with NetworkManager…"
    nmcli device set "$found" managed yes 2>/dev/null || true
    if nmcli device connect "$found" 2>/dev/null; then
        ok "nmcli device connect ${found} succeeded"
    else
        warn "nmcli device connect failed — trying connection up if a profile exists"
        local con
        con="$(nm_con_for_device "$found")"
        if [[ -n "$con" ]]; then
            nmcli connection up "$con" || warn "connection up failed for $con"
        else
            # Create a simple ethernet profile for the USB NIC
            con="sccs-usb-tether"
            nmcli connection delete "$con" 2>/dev/null || true
            nmcli connection add type ethernet ifname "$found" con-name "$con" \
                ipv4.method auto ipv6.method ignore connection.autoconnect yes \
                || warn "Could not create connection profile"
            nmcli connection up "$con" || warn "Could not bring up $con"
        fi
    fi

    sleep 2
    info "Addresses after connect:"
    ip -4 addr show dev "$found" 2>/dev/null | sed 's/^/    /' || warn "No IPv4 on $found yet"

    # Wi-Fi is the default uplink (lower metric wins). USB is the fallback
    # unless this Pi was already set to prefer the hotspot.
    ensure_wifi_uplink_default
    local tether_con wlan_con prefer="wifi" pref_file="$SCCS_HOME/config/preferred_uplink.json"
    if [[ -f "$pref_file" ]]; then
        prefer="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("prefer","wifi"))' "$pref_file" 2>/dev/null || echo wifi)"
    fi
    [[ "$prefer" == "usb" ]] || prefer="wifi"
    tether_con="$(nm_con_for_device "$found")"
    wlan_con=""
    if ip link show wlan0 &>/dev/null; then
        wlan_con="$(nm_con_for_device wlan0)"
    fi
    if [[ "$prefer" == "wifi" ]]; then
        if [[ -n "$wlan_con" ]]; then
            nmcli connection modify "$wlan_con" ipv4.route-metric 50
            ok "Route metric 50 on Wi-Fi '${wlan_con}' (preferred uplink)"
            nmcli connection up "$wlan_con" 2>/dev/null || true
        fi
        if [[ -n "$tether_con" ]]; then
            nmcli connection modify "$tether_con" ipv4.route-metric 200 connection.autoconnect yes
            ok "Route metric 200 on '${tether_con}' (fallback)"
            nmcli connection up "$tether_con" 2>/dev/null || true
        else
            warn "No NM connection name for $found — skip metric tweak"
        fi
    else
        if [[ -n "$tether_con" ]]; then
            nmcli connection modify "$tether_con" ipv4.route-metric 50 connection.autoconnect yes
            ok "Route metric 50 on '${tether_con}' (preferred uplink)"
            nmcli connection up "$tether_con" 2>/dev/null || true
        else
            warn "No NM connection name for $found — skip metric tweak"
        fi
        if [[ -n "$wlan_con" ]]; then
            nmcli connection modify "$wlan_con" ipv4.route-metric 200
            ok "Route metric 200 on Wi-Fi '${wlan_con}' (fallback)"
            nmcli connection up "$wlan_con" 2>/dev/null || true
        fi
    fi

    echo
    info "Default routes:"
    ip route show default 2>/dev/null | sed 's/^/    /' || true

    # Rebuild LAN NAT/DHCP with all WAN ifaces
    local wans=()
    ip link show wlan0 &>/dev/null && wans+=(wlan0)
    wans+=("$found")
    info "Refreshing NAT/DHCP for WAN: ${wans[*]}"
    LAN_ADDR="$LAN_ADDR" \
    DHCP_RANGE_START="${DHCP_RANGE_START:-10.10.10.50}" \
    DHCP_RANGE_END="${DHCP_RANGE_END:-10.10.10.200}" \
    SCCS_CONF="$CONF" \
    WAN_IFS="${wans[*]}" \
        apply_lan_nat_dhcp

    ok "Phone USB tether configured on ${found}"
    info "LAN clients should reach the internet via the Pi. Test: ping 8.8.8.8 from a LAN device."
}

# ---------------------------------------------------------------------------
# Touchscreens: LAN scan → pick client → write [screens] → SSH + desktop login + Chromium UI
# ---------------------------------------------------------------------------

# Print existing [screens] rows as TSV: name host user blank mac friendly
list_configured_screens() {
    python3 - "$CONF" <<'PY'
import configparser, sys
cfg = configparser.ConfigParser()
cfg.read(sys.argv[1])
if not cfg.has_section("screens"):
    sys.exit(0)
for name, line in cfg.items("screens"):
    parts = [p.strip() for p in str(line).split("|")]
    if len(parts) < 4:
        continue
    friendly = parts[0]
    host = parts[2]
    user = parts[3]
    blank = parts[9] if len(parts) > 9 else ""
    mac = parts[10] if len(parts) > 10 else ""
    if blank.lower() in ("none", "-", ""):
        blank = ""
    if mac.lower() in ("none", "-", ""):
        mac = ""
    print(f"{name}\t{host}\t{user}\t{blank}\t{mac}\t{friendly}")
PY
}

# Upsert one [screens] entry (adds new key or replaces existing / commented line).
# Args: name value_line  (value_line is everything after "name = ")
conf_upsert_screen() {
    local name="$1" value="$2"
    [[ -f "$CONF" ]] || die "No config at $CONF — run Install SCCS first"
    python3 - "$CONF" "$name" "$value" <<'PY'
import re, sys

def section_span(text, section):
    header = re.compile(rf"(?m)^\[{re.escape(section)}\][ \t]*\r?\n")
    m = header.search(text)
    if not m:
        return None
    start = m.start()
    nxt = re.compile(r"(?m)^\[[^\]]+\][ \t]*\r?\n")
    m2 = nxt.search(text, m.end())
    end = m2.start() if m2 else len(text)
    return start, end

path, name, value = sys.argv[1], sys.argv[2], sys.argv[3]
if not re.fullmatch(r"[a-z][a-z0-9_]*", name):
    sys.exit(f"invalid screen name: {name!r} (use lowercase letters, digits, underscore)")
text = open(path, encoding="utf-8").read()
span = section_span(text, "screens")
if not span:
    sys.exit(f"section [screens] not found in {path}")
start, end = span
block = text[start:end]
# Active key
pat_active = re.compile(rf"(?m)^({re.escape(name)}[ \t]*=[ \t]*)(.*)$")
# Commented example / old entry
pat_comment = re.compile(rf"(?m)^#[ \t]*({re.escape(name)}[ \t]*=[ \t]*)(.*)$")
if pat_active.search(block):
    block = pat_active.sub(lambda mo: mo.group(1) + value, block, count=1)
elif pat_comment.search(block):
    block = pat_comment.sub(lambda mo: mo.group(1) + value, block, count=1)
else:
    # Append before trailing blank lines of the section
    insert = f"{name:<18}= {value}\n"
    block = block.rstrip("\n") + "\n" + insert + "\n"
text = text[:start] + block + text[end:]
open(path, "w", encoding="utf-8").write(text)
print(f"wrote [screens] {name}")
PY
    chown "$USERNAME":www-data "$CONF" 2>/dev/null || true
    SCREEN_CONFIG_CHANGED=1
}

# Remove one [screens] key (used when renaming internal name on re-setup).
conf_remove_screen() {
    local name="$1"
    [[ -f "$CONF" ]] || return 0
    python3 - "$CONF" "$name" <<'PY'
import re, sys
path, name = sys.argv[1], sys.argv[2]
text = open(path, encoding="utf-8").read()
header = re.compile(r"(?m)^\[screens\][ \t]*\r?\n")
m = header.search(text)
if not m:
    sys.exit(0)
start = m.start()
nxt = re.compile(r"(?m)^\[[^\]]+\][ \t]*\r?\n")
m2 = nxt.search(text, m.end())
end = m2.start() if m2 else len(text)
block = text[start:end]
pat = re.compile(rf"(?m)^{re.escape(name)}[ \t]*=[ \t]*.*\r?\n?")
block2, n = pat.subn("", block, count=1)
if n:
    text = text[:start] + block2 + text[end:]
    open(path, "w", encoding="utf-8").write(text)
    print(f"removed [screens] {name}")
PY
    chown "$USERNAME":www-data "$CONF" 2>/dev/null || true
    SCREEN_CONFIG_CHANGED=1
}

# Interactive: pick a reed for a screen. Args: current_reed (may be empty).
# Sets REPLY_REED.
pick_screen_reed() {
    local current="${1:-}"
    local -a REED_NAMES=() REED_LABELS=()
    local i ans rn rl def_idx=0
    REPLY_REED="$current"

    mapfile -t REED_ROWS < <(python3 - "$CONF" <<'PY'
import configparser, sys
cfg = configparser.ConfigParser()
cfg.read(sys.argv[1])
if not cfg.has_section("reeds"):
    sys.exit(0)
for name, line in cfg.items("reeds"):
    parts = [p.strip() for p in str(line).split("|")]
    label = parts[0] if parts else name
    print(f"{name}\t{label}")
PY
)
    if [[ ${#REED_ROWS[@]} -eq 0 ]]; then
        read -r -p "  Linked reed internal name [${current:-none}]: " ans || true
        if [[ -n "${ans:-}" ]]; then
            REPLY_REED="$ans"
        fi
        return 0
    fi

    echo
    info "Link to a reed (door/panel sensor), or 0 for none:"
    printf "  ${C_BOLD}[0]${C_RESET}  (none)\n"
    for i in "${!REED_ROWS[@]}"; do
        IFS=$'\t' read -r rn rl <<<"${REED_ROWS[$i]}"
        REED_NAMES+=("$rn")
        REED_LABELS+=("$rl")
        mark=""
        if [[ -n "$current" && "$rn" == "$current" ]]; then
            mark=" ${C_GREEN}← current${C_RESET}"
            def_idx=$((i + 1))
        fi
        printf "  ${C_BOLD}[%d]${C_RESET}  %s  (%s)%b\n" "$((i + 1))" "$rn" "$rl" "$mark"
    done
    while true; do
        read -r -p "  Reed [${def_idx}]: " ans || true
        ans="${ans:-$def_idx}"
        if [[ "$ans" == "0" ]]; then
            REPLY_REED=""
            break
        fi
        if [[ "$ans" =~ ^[0-9]+$ ]] && (( ans >= 1 && ans <= ${#REED_NAMES[@]} )); then
            REPLY_REED="${REED_NAMES[$((ans - 1))]}"
            break
        fi
        warn "Pick 0–${#REED_NAMES[@]}"
    done
}

# Classify an SSH identification string.
# Prints debian, ubuntu, raspbian, or armbian. Armbian panels ship Debian or
# Ubuntu OpenSSH, so the banner carries that suffix. Windows OpenSSH and
# non-SSH clients are not panels.
linux_banner_os() {
    local banner="${1//$'\r'/}"
    local low="${banner,,}"
    [[ "$low" == ssh-2.0-openssh* ]] || return 1
    [[ "$low" == *windows* ]] && return 1
    if [[ "$low" == *armbian* ]]; then
        printf 'armbian'
        return 0
    fi
    if [[ "$low" == *debian* ]]; then
        printf 'debian'
        return 0
    fi
    if [[ "$low" == *ubuntu* ]]; then
        printf 'ubuntu'
        return 0
    fi
    if [[ "$low" == *raspbian* ]]; then
        printf 'raspbian'
        return 0
    fi
    return 1
}

# Read host:22's SSH banner and print the Linux/Armbian tag, or fail.
linux_panel_os() {
    local host="$1" banner=""
    [[ "$host" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] || return 1
    banner="$(timeout 2 bash -c '
        exec 3<>"/dev/tcp/${1}/22" || exit 1
        IFS= read -r -t 2 line <&3 || exit 1
        printf "%s" "$line"
    ' _ "$host" 2>/dev/null || true)"
    linux_banner_os "$banner"
}

# Scan wired LAN for live clients, then keep Linux/Armbian SSH hosts.
# Prints TSV: ip\tmac\thostname\tsource
# Sources: dhcp (Pi-hole lease), arp (neighbour table), plus debian/ubuntu/armbian.
scan_wired_lan_clients() {
    local lan_if="$1"
    local self_ip="${2:-$LAN_ADDR}"
    local cidr_suffix start end i ip
    local lease_file="/etc/pihole/dhcp.leases"
    local tmp_merge tmp_rows tmp_os

    tmp_merge="$(mktemp)"
    tmp_rows="$(mktemp)"
    tmp_os="$(mktemp -d)"
    # Seed from Pi-hole DHCP leases (includes hostname even if currently quiet)
    if [[ -r "$lease_file" ]]; then
        # expiry mac ip hostname client-id
        awk -v self="$self_ip" '
            NF >= 4 {
                mac=tolower($2); ip=$3; host=$4
                if (ip == self || ip == "0.0.0.0") next
                if (host == "*" || host == "") host="-"
                printf "%s\t%s\t%s\tdhcp\n", ip, mac, host
            }
        ' "$lease_file" >>"$tmp_merge" 2>/dev/null || true
    fi

    # Ping-sweep the LAN /24 (or declared DHCP range) to populate ARP, then re-read neigh.
    # Prefer actual iface prefix length; fall back to 10.10.10.0/24 style from LAN_ADDR.
    local prefix net_base
    prefix="$(ip -4 -o addr show dev "$lan_if" 2>/dev/null \
        | awk '{print $4; exit}')"
    if [[ "$prefix" =~ ^([0-9]+)\.([0-9]+)\.([0-9]+)\.([0-9]+)/([0-9]+)$ ]]; then
        net_base="${BASH_REMATCH[1]}.${BASH_REMATCH[2]}.${BASH_REMATCH[3]}"
        if [[ "${BASH_REMATCH[5]}" -ge 24 ]]; then
            start=1
            end=254
        else
            start="${DHCP_RANGE_START##*.}"
            end="${DHCP_RANGE_END##*.}"
            start="${start:-50}"
            end="${end:-200}"
            net_base="${self_ip%.*}"
        fi
    else
        net_base="${self_ip%.*}"
        start="${DHCP_RANGE_START##*.}"
        end="${DHCP_RANGE_END##*.}"
        start="${start:-50}"
        end="${end:-200}"
    fi

    # Never use info()/ok() here — they write status that callers capture as TSV.
    # stdout must be pure client rows only.
    echo "  Probing ${net_base}.${start}–${net_base}.${end} on ${lan_if}…" >&2
    # Parallel quiet pings (cap concurrency via xargs -P)
    seq "$start" "$end" \
        | xargs -P 64 -I{} ping -c1 -W1 -I "$lan_if" "${net_base}.{}" >/dev/null 2>&1 || true

    # Neighbour / ARP table on the LAN iface
    ip neigh show dev "$lan_if" 2>/dev/null \
        | awk -v self="$self_ip" '
            $1 ~ /^[0-9]+\./ && $NF != "FAILED" && $NF != "INCOMPLETE" {
                ip=$1; mac="-"
                for (i=1;i<=NF;i++) if ($i=="lladdr") mac=tolower($(i+1))
                if (ip == self) next
                if (mac == "-" || mac == "") next
                printf "%s\t%s\t-\tarp\n", ip, mac
            }
        ' >>"$tmp_merge"

    # Merge by IP (prefer dhcp hostname; union sources)
    python3 - "$tmp_merge" >"$tmp_rows" <<'PY'
import sys
from collections import OrderedDict
path = sys.argv[1]
rows = OrderedDict()  # ip -> dict
with open(path, encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) < 4:
            continue
        ip, mac, host, src = parts[0], parts[1].lower(), parts[2], parts[3]
        rec = rows.get(ip)
        if rec is None:
            rows[ip] = {"ip": ip, "mac": mac, "host": host, "src": {src}}
            continue
        if mac and mac != "-" and (not rec["mac"] or rec["mac"] == "-"):
            rec["mac"] = mac
        elif mac and mac != "-" and rec["mac"] not in ("", "-") and mac != rec["mac"]:
            # keep first mac, note clash in src
            rec["src"].add("mac-clash")
        if host and host != "-" and (not rec["host"] or rec["host"] == "-"):
            rec["host"] = host
        rec["src"].add(src)

# Optional reverse DNS via system resolver (Pi-hole when configured)
import socket
for ip, rec in rows.items():
    if rec["host"] in ("", "-", None):
        try:
            name = socket.gethostbyaddr(ip)[0]
            if name:
                rec["host"] = name.split(".")[0]
                rec["src"].add("rdns")
        except Exception:
            pass

def sort_key(item):
    ip = item[0]
    try:
        return tuple(int(x) for x in ip.split("."))
    except Exception:
        return (999, 999, 999, 999)

for ip, rec in sorted(rows.items(), key=sort_key):
    host = rec["host"] if rec["host"] and rec["host"] != "-" else "-"
    mac = rec["mac"] if rec["mac"] else "-"
    src = "+".join(sorted(rec["src"]))
    print(f"{ip}\t{mac}\t{host}\t{src}")
PY

    echo "  Checking SSH banners — keeping Linux/Armbian panels…" >&2
    local idx=0 ip mac host src id
    local -a probe_pids=()
    while IFS=$'\t' read -r ip mac host src; do
        [[ "$ip" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || continue
        printf -v id '%04d' "$idx"
        (
            local os_tag=""
            if os_tag="$(linux_panel_os "$ip")"; then
                printf '%s\t%s\t%s\t%s+%s\n' "$ip" "$mac" "$host" "$src" "$os_tag" \
                    >"${tmp_os}/${id}"
            fi
        ) &
        probe_pids+=("$!")
        idx=$((idx + 1))
        if (( ${#probe_pids[@]} >= 24 )); then
            wait "${probe_pids[0]}" || true
            probe_pids=("${probe_pids[@]:1}")
        fi
    done <"$tmp_rows"
    wait || true
    local hit
    for hit in "${tmp_os}"/*; do
        [[ -f "$hit" ]] || continue
        cat "$hit"
    done
    rm -f "$tmp_merge" "$tmp_rows"
    rm -rf "$tmp_os"
}

# True if host:22 accepts TCP (sshd listening).
panel_ssh_port_open() {
    local host="$1" timeout_s="${2:-3}"
    # bash /dev/tcp is enough; prefer nc/timeout if available
    if command -v timeout >/dev/null 2>&1; then
        timeout "$timeout_s" bash -c "echo >/dev/tcp/${host}/22" 2>/dev/null
    else
        bash -c "echo >/dev/tcp/${host}/22" 2>/dev/null
    fi
}

# Pick the first candidate that accepts TCP :22 (preference order = arg order).
# Prints the chosen IP. Returns 0 if any port is open, 1 if none are
# (still prints the first non-empty candidate as a best-effort target).
# Use for reserved/desired IP first, then live/discovered lease.
resolve_panel_ssh_target() {
    local cand first=""
    for cand in "$@"; do
        [[ -n "$cand" && "$cand" != "-" ]] || continue
        # de-dupe consecutive / repeated candidates
        if [[ "$cand" == "$first" ]]; then
            continue
        fi
        if [[ -z "$first" ]]; then
            first="$cand"
        fi
        if panel_ssh_port_open "$cand" 2; then
            printf '%s' "$cand"
            return 0
        fi
    done
    printf '%s' "${first:-}"
    return 1
}

# Drop the panel's current DHCP lease so Pi-hole hands out the reserved address.
# The link goes down, so the renew runs detached and this returns without waiting
# for the new address. Call wait_panel_ssh on the reserved IP afterwards.
# Args: user host [password]
force_panel_dhcp_renew() {
    local screen_user="$1" screen_host="$2" screen_pass="${3:-}"
    run_user_bash env \
        SCREEN_USER="$screen_user" \
        SCREEN_HOST="$screen_host" \
        SCREEN_PASS="${screen_pass:-}" \
        <<'EOS'
set -euo pipefail
KEY="$HOME/.ssh/sccs_screen"
KNOWN_HOSTS="$HOME/.sccs/screen_known_hosts"
SSH_BATCH=(
    -o BatchMode=yes
    -o PreferredAuthentications=publickey
    -o IdentitiesOnly=yes
    -o UserKnownHostsFile="${KNOWN_HOSTS}"
    -o StrictHostKeyChecking=accept-new
    -o ConnectTimeout=15
    -i "${KEY}"
)
[[ -f "$KEY" ]] || { echo "Missing $KEY" >&2; exit 1; }

renew_script=$(cat <<'RENEW'
#!/bin/bash
# One-shot: release the current lease and request again (picks up a static reservation).
exec >/tmp/sccs-dhcp-renew.log 2>&1
set +e
echo "sccs dhcp renew $(date -Is 2>/dev/null || date)"
# Let the installer's SSH session exit before the interface drops.
sleep 2
ifaces=()
for d in /sys/class/net/*; do
  n=$(basename "$d")
  case "$n" in
    lo|wlan*|docker*|veth*|br-*|virbr*|tailscale*|zt*|wg*) continue ;;
  esac
  carrier=$(cat "/sys/class/net/$n/carrier" 2>/dev/null || echo 0)
  [[ "$carrier" == "1" ]] || continue
  ifaces+=("$n")
done
[[ ${#ifaces[@]} -eq 0 ]] && ifaces=(eth0)
echo "ifaces: ${ifaces[*]}"
renewed=0
# reapply keeps the current lease. Disconnect forces a new DHCP request.
if command -v nmcli >/dev/null 2>&1; then
  for n in "${ifaces[@]}"; do
    echo "nmcli disconnect/connect $n"
    nmcli device disconnect "$n" || true
    sleep 1
    if nmcli device connect "$n"; then
      renewed=1
      break
    fi
  done
fi
if [[ "$renewed" -eq 0 ]] && command -v dhcpcd >/dev/null 2>&1; then
  for n in "${ifaces[@]}"; do
    echo "dhcpcd -k/-n $n"
    dhcpcd -k "$n" || true
    sleep 1
    if dhcpcd -n "$n" || dhcpcd "$n"; then
      renewed=1
      break
    fi
  done
fi
if [[ "$renewed" -eq 0 ]] && command -v networkctl >/dev/null 2>&1; then
  for n in "${ifaces[@]}"; do
    echo "networkctl reconfigure $n"
    if networkctl reconfigure "$n"; then
      renewed=1
      break
    fi
  done
fi
if [[ "$renewed" -eq 0 ]] && command -v dhclient >/dev/null 2>&1; then
  for n in "${ifaces[@]}"; do
    echo "dhclient -r/- $n"
    dhclient -r "$n" || true
    sleep 1
    if dhclient "$n"; then
      renewed=1
      break
    fi
  done
fi
echo "renewed=$renewed"
ip -4 -br addr 2>/dev/null || ip -4 addr
RENEW
)

if ! ssh "${SSH_BATCH[@]}" "${SCREEN_USER}@${SCREEN_HOST}" \
    "cat > /tmp/sccs-dhcp-renew.sh && chmod 755 /tmp/sccs-dhcp-renew.sh" \
    <<<"$renew_script"; then
    echo "Could not upload DHCP renew script" >&2
    exit 1
fi

# systemd-run survives the SSH session dying when the address changes.
launch='systemd-run --quiet --collect --unit=sccs-dhcp-renew /bin/bash /tmp/sccs-dhcp-renew.sh || setsid /bin/bash /tmp/sccs-dhcp-renew.sh </dev/null >/dev/null 2>&1 &'
if [[ -n "${SCREEN_PASS:-}" ]]; then
    printf '%s\n' "$SCREEN_PASS" | ssh "${SSH_BATCH[@]}" "${SCREEN_USER}@${SCREEN_HOST}" \
        "sudo -S -p '' bash -c $(printf '%q' "$launch")"
else
    ssh "${SSH_BATCH[@]}" "${SCREEN_USER}@${SCREEN_HOST}" \
        "sudo -n bash -c $(printf '%q' "$launch")"
fi
echo "DHCP renew started on ${SCREEN_USER}@${SCREEN_HOST}"
EOS
}

# Poll host:22 until open or timeout. Returns 0 when reachable.
wait_panel_ssh() {
    local host="$1" timeout_s="${2:-45}" interval=2 elapsed=0
    [[ -n "$host" && "$host" != "-" ]] || return 1
    if panel_ssh_port_open "$host" 2; then
        return 0
    fi
    while (( elapsed < timeout_s )); do
        sleep "$interval"
        elapsed=$((elapsed + interval))
        if panel_ssh_port_open "$host" 2; then
            return 0
        fi
    done
    return 1
}

# Collect this host's global IPv4 addresses (comma-separated). Always includes LAN_ADDR.
host_global_ipv4s() {
    local self="${1:-$LAN_ADDR}"
    local ips
    ips="$(ip -4 -o addr show scope global 2>/dev/null \
        | awk '{split($4,a,"/"); print a[1]}' \
        | awk 'NF' | sort -u | paste -sd, -)"
    if [[ -n "$self" ]]; then
        if [[ -n "$ips" ]]; then
            printf '%s,%s\n' "$ips" "$self"
        else
            printf '%s\n' "$self"
        fi
    else
        printf '%s\n' "$ips"
    fi
}

# Validate IPv4 on the van LAN (same /24 as LAN_ADDR by default).
# Prints normalized IP on stdout, or reason on stderr and fails.
# Rejects: invalid IPv4, host Pi addresses (LAN_ADDR + all local global IPv4s),
# loopback/multicast/link-local/unspecified, network & broadcast of the LAN /24.
validate_lan_ip() {
    local candidate="$1" self="${2:-$LAN_ADDR}"
    local host_ips
    host_ips="$(host_global_ipv4s "$self")"
    python3 - "$candidate" "$self" "$host_ips" <<'PY'
import ipaddress, sys

cand, self, host_csv = sys.argv[1], sys.argv[2], sys.argv[3]

def fail(msg: str) -> None:
    print(msg, file=sys.stderr)
    sys.exit(1)

try:
    ip = ipaddress.IPv4Address(cand.strip())
except Exception:
    fail(f"not a valid IPv4 address ({cand!r})")

try:
    gw = ipaddress.IPv4Address(self.strip())
except Exception:
    fail(f"gateway/LAN_ADDR is not a valid IPv4 ({self!r})")

host_ips = {gw}
for part in host_csv.split(","):
    part = part.strip()
    if not part:
        continue
    try:
        host_ips.add(ipaddress.IPv4Address(part))
    except Exception:
        pass

if ip in host_ips:
    fail(f"{ip} is this host Pi (cannot reserve the SCCS LAN address)")
if ip.is_unspecified:
    fail(f"{ip} is unspecified (0.0.0.0)")
if ip.is_loopback:
    fail(f"{ip} is loopback")
if ip.is_multicast:
    fail(f"{ip} is multicast")
if ip.is_link_local:
    fail(f"{ip} is link-local")
if ip.is_reserved and not ip.is_private:
    fail(f"{ip} is reserved")

# Same /24 as gateway (van LAN)
net = ipaddress.IPv4Network(f"{gw}/24", strict=False)
if ip not in net:
    fail(f"{ip} is not on the van LAN {net}")
if ip == net.network_address:
    fail(f"{ip} is the network address of {net}")
if ip == net.broadcast_address:
    fail(f"{ip} is the broadcast address of {net}")

print(ip)
PY
}

# If $1 is already the host of another [screens] entry, print that screen name and return 0.
# Optional $2 = screen name to exclude (current panel being edited).
# Returns 1 if free.
screen_host_ip_taken() {
    local candidate="$1" exclude="${2:-}"
    local conf_path="${3:-$CONF}"
    [[ -n "$candidate" && -f "$conf_path" ]] || return 1
    python3 - "$conf_path" "$candidate" "$exclude" <<'PY'
import configparser, ipaddress, sys

path, cand, exclude = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    want = str(ipaddress.IPv4Address(cand.strip()))
except Exception:
    sys.exit(1)

cfg = configparser.ConfigParser()
cfg.read(path)
if not cfg.has_section("screens"):
    sys.exit(1)
for name, line in cfg.items("screens"):
    if exclude and name == exclude:
        continue
    parts = [p.strip() for p in str(line).split("|")]
    if len(parts) < 3:
        continue
    try:
        host = str(ipaddress.IPv4Address(parts[2]))
    except Exception:
        continue
    if host == want:
        print(name)
        sys.exit(0)
sys.exit(1)
PY
}

# Interactive reserved-IP prompt used by new + existing screen setup.
# Sets REPLY_LAN_IP on success. $1 = default IP, $2 = screen name to exclude from conflicts.
prompt_reserved_lan_ip() {
    local default_ip="$1" exclude_name="${2:-}"
    local reserved result owner
    while true; do
        read -r -p "  Reserved IP [${default_ip}]: " reserved || true
        reserved="${reserved:-$default_ip}"
        reserved="${reserved// /}"
        # Capture stdout+stderr; success → normalized IP, failure → reason text.
        if result="$(validate_lan_ip "$reserved" "$LAN_ADDR" 2>&1)"; then
            if owner="$(screen_host_ip_taken "$result" "$exclude_name")"; then
                warn "IP ${result} is already used by screen '${owner}' — pick another"
                continue
            fi
            REPLY_LAN_IP="$result"
            return 0
        fi
        if [[ -n "$result" ]]; then
            warn "Invalid LAN IP: ${result}"
        else
            warn "Invalid LAN IP (must be on ${LAN_ADDR%.*}.0/24, not the host Pi ${LAN_ADDR})"
        fi
    done
}

# Interactive: define one scanned (or manual) client as a touchscreen in conf + SSH.
# Args: discovered_ip mac hostname (hostname may be -)
define_touchscreen_from_client() {
    local discovered_ip="$1" mac="$2" hostname="${3:--}"
    local ip="$discovered_ip"
    local name friendly reed user bright blank icon day evening night
    local alias skip_blank blank_path pass="" setup_ssh=1
    local -a REED_NAMES=() REED_LABELS=()
    local i ans def_name def_friendly reserved

    echo
    section_title "Define touchscreen"
    info "Selected: ${C_BOLD}${discovered_ip}${C_RESET}  mac=${mac}  hostname=${hostname}"

    # Suggested names. Hostname is shown above; these are the examples to accept or edit.
    def_name="kitchen"
    def_friendly="Kitchen Touchscreen"

    while true; do
        read -r -p "  Internal name (config key) [${def_name}]: " name || true
        name="${name:-$def_name}"
        name="$(printf '%s' "$name" | tr '[:upper:]' '[:lower:]' | sed -E 's/[^a-z0-9_]+/_/g; s/^_+//; s/_+$//')"
        if [[ "$name" =~ ^[a-z][a-z0-9_]*$ ]]; then
            break
        fi
        warn "Use lowercase letters, digits, underscore (e.g. kitchen)"
    done

    read -r -p "  Friendly name [${def_friendly}]: " friendly || true
    friendly="${friendly:-$def_friendly}"

    # Fixed DHCP reservation IP (what SCCS and Pi-hole will use long-term)
    echo
    info "DHCP reservation — fixed IP for this panel (MAC → IP in Pi-hole)."
    info "Current lease: ${discovered_ip}. Prefer a stable address (e.g. outside the pool ${DHCP_RANGE_START:-10.10.10.50}–${DHCP_RANGE_END:-10.10.10.200})."
    info "Host Pi ${LAN_ADDR} (and this machine's other LAN IPs) cannot be reserved."
    prompt_reserved_lan_ip "$discovered_ip" "$name"
    ip="$REPLY_LAN_IP"
    if [[ "$ip" != "$discovered_ip" ]]; then
        info "Will reserve ${mac} → ${ip} (panel currently at ${discovered_ip}; may need renew/reboot to move)"
    else
        info "Will reserve ${mac:--} → ${ip}"
    fi

    # Linked reed (optional but recommended)
    mapfile -t REED_ROWS < <(python3 - "$CONF" <<'PY'
import configparser, sys
cfg = configparser.ConfigParser()
cfg.read(sys.argv[1])
if not cfg.has_section("reeds"):
    sys.exit(0)
for name, line in cfg.items("reeds"):
    parts = [p.strip() for p in str(line).split("|")]
    label = parts[0] if parts else name
    print(f"{name}\t{label}")
PY
)
    reed=""
    if [[ ${#REED_ROWS[@]} -gt 0 ]]; then
        echo
        info "Link to a reed (door/panel sensor), or 0 for none:"
        printf "  ${C_BOLD}[0]${C_RESET}  (none)\n"
        for i in "${!REED_ROWS[@]}"; do
            IFS=$'\t' read -r rn rl <<<"${REED_ROWS[$i]}"
            REED_NAMES+=("$rn")
            REED_LABELS+=("$rl")
            printf "  ${C_BOLD}[%d]${C_RESET}  %s  (%s)\n" "$((i + 1))" "$rn" "$rl"
        done
        while true; do
            read -r -p "  Reed [0]: " ans || true
            ans="${ans:-0}"
            if [[ "$ans" == "0" ]]; then
                reed=""
                break
            fi
            if [[ "$ans" =~ ^[0-9]+$ ]] && (( ans >= 1 && ans <= ${#REED_NAMES[@]} )); then
                reed="${REED_NAMES[$((ans - 1))]}"
                break
            fi
            warn "Pick 0–${#REED_NAMES[@]}"
        done
    else
        read -r -p "  Linked reed internal name (empty = none): " reed || true
    fi

    # SSH reachability before asking for a password.
    # Prefer the reserved (desired) IP when it already answers — the panel may
    # already hold that address from a prior reservation or static config.
    # Fall back to the discovered lease so first-time setup still works before
    # the panel has renewed DHCP onto the reserved address.
    local ssh_target
    echo
    if ssh_target="$(resolve_panel_ssh_target "$ip" "$discovered_ip")"; then
        if [[ "$ip" != "$discovered_ip" && "$ssh_target" == "$ip" ]]; then
            ok "SSH port open on reserved IP ${ssh_target} (panel already at desired address)"
        elif [[ "$ip" != "$discovered_ip" && "$ssh_target" == "$discovered_ip" ]]; then
            ok "SSH port open on ${ssh_target} (panel still on current lease; reserved ${ip} after renew)"
        else
            ok "SSH port open on ${ssh_target}"
        fi
    else
        ssh_target="${ssh_target:-$discovered_ip}"
        warn "Nothing is accepting connections on ${ssh_target}:22 (Connection refused / filtered)."
        if [[ "$ip" != "$discovered_ip" ]]; then
            info "Checked reserved ${ip} and current lease ${discovered_ip}."
        fi
        info "On the panel, enable SSH then retry, e.g.:"
        info "  sudo systemctl enable --now ssh   # or: sudo raspi-config → Interface → SSH"
        info "  sudo systemctl status ssh"
        echo
        echo "  ${C_CYAN}1${C_RESET}  Wait / retry SSH check"
        echo "  ${C_CYAN}2${C_RESET}  Save config + DHCP reservation only (set up SSH later via menu 8 → option 2)"
        echo "  ${C_CYAN}q${C_RESET}  Cancel"
        while true; do
            read -r -p "  Choice [1]: " ans || true
            ans="${ans:-1}"
            case "${ans,,}" in
                1|r|retry)
                    if ssh_target="$(resolve_panel_ssh_target "$ip" "$discovered_ip")"; then
                        ok "SSH port is open on ${ssh_target}"
                        break
                    fi
                    ssh_target="${ssh_target:-$discovered_ip}"
                    if [[ "$ip" != "$discovered_ip" ]]; then
                        warn "Still not open (tried reserved ${ip} and lease ${discovered_ip})"
                    else
                        warn "Still not open on ${ssh_target}:22"
                    fi
                    ;;
                2)
                    setup_ssh=0
                    user="${USERNAME}"
                    pass=""
                    break
                    ;;
                q|quit|s|skip|n|no)
                    skip_note "Screen setup cancelled"
                    return 0
                    ;;
                *)
                    warn "Pick 1, 2, or q"
                    ;;
            esac
        done
    fi

    if [[ "$setup_ssh" -eq 1 ]]; then
        pass=""
        if load_screen_password "$name" "$ssh_target" ""; then
            pass="$REPLY_PASS"
        elif [[ "$ip" != "$ssh_target" ]] && load_screen_password "$name" "$ip" ""; then
            pass="$REPLY_PASS"
        fi
        REPLY_PASS=""
        if [[ -n "$pass" ]]; then
            info "Using the saved panel password for ${name}"
            [[ -n "${REPLY_CRED_USER:-}" ]] && user="$REPLY_CRED_USER"
            if ! verify_panel_ssh "${user:-$USERNAME}" "$ssh_target" "$pass"; then
                warn "Saved panel password was not accepted for ${name}"
                forget_screen_credentials "$name"
                pass=""
            fi
        fi
        if [[ -z "$pass" ]]; then
            prompt_panel_ssh_credentials "${user:-$USERNAME}" "${ssh_target}"
            user="$REPLY_USER"
            pass="$REPLY_PASS"
            # Retry password / abort — never continue with bad creds
            if ! verify_panel_ssh "$user" "$ssh_target" "$pass"; then
                unset pass REPLY_PASS 2>/dev/null || true
                return 1
            fi
            user="$REPLY_USER"
            pass="$REPLY_PASS"
        else
            user="${user:-$USERNAME}"
        fi
        save_screen_credentials "$name" "$ip" "$user" "$pass" || true
    else
        read -r -p "  SSH username to store in config [${USERNAME}]: " user || true
        user="${user:-$USERNAME}"
    fi

    # Probe display controls only after SSH is verified
    local bright blank method notes
    bright="/sys/class/graphics/fb0/blank"
    blank="/sys/class/graphics/fb0/blank"
    method="fallback-fb"
    if [[ "$setup_ssh" -eq 1 && -n "$pass" ]]; then
        if probe_panel_display_controls "$user" "$ssh_target" "$pass"; then
            bright="${REPLY_BRIGHT:-$bright}"
            blank="${REPLY_BLANK:-$blank}"
            method="${REPLY_METHOD:-}"
            notes="${REPLY_PROBE_NOTES:-}"
        else
            # SSH works; panel just has no recognised controls
            info "No display controls detected — using framebuffer blank defaults"
            method="fallback-fb"
        fi
    else
        info "Skipping display probe until SSH works — using framebuffer defaults"
    fi

    # Sensible defaults — edit [screens] in sccs.conf later if needed (no prompts).
    icon="fa-display"
    if [[ "$reed" == *kitchen* ]]; then
        icon="fa-utensils"
    elif [[ "$reed" == *storage* ]]; then
        icon="fa-boxes-stacked"
    fi
    day=100
    evening=30
    night=5

    if [[ -z "$mac" || "$mac" == "-" ]]; then
        warn "No MAC learned — DHCP reservation will be skipped until MAC is known"
        mac="-"
    fi

    # Config host = reserved IP (Pi-hole reservation + SCCS SSH target long-term)
    local value_line
    value_line="${friendly} | ${reed} | ${ip} | ${user} | ${bright} | ${icon} | ${day} | ${evening} | ${night} | ${blank} | ${mac}"

    echo
    info "Display: ${method:-unknown}  bright=${bright}  blank=${blank}"
    [[ -n "${notes:-}" ]] && info "${notes}"
    info "Writing [screens] ${name} (edit sccs.conf later to tweak paths / brightness %)"
    echo "  ${C_DIM}${name} = ${value_line}${C_RESET}"
    if [[ "$setup_ssh" -eq 1 ]]; then
        info "Provisioning passwordless SSH and desktop login on ${user}@${ssh_target} (reserved IP ${ip})…"
    else
        info "SSH setup skipped — reservation + config only."
    fi

    conf_upsert_screen "$name" "$value_line"
    ok "Config updated: [screens] ${name}"

    if command -v pihole >/dev/null 2>&1 || systemctl is-active --quiet pihole-FTL 2>/dev/null; then
        info "Applying Pi-hole DHCP reservation ${mac} → ${ip}…"
        apply_pihole_screen_reservations "$CONF"
    fi

    if [[ "$setup_ssh" -ne 1 ]]; then
        ok "Screen ${name} saved (SSH later: menu 8 → option 2)"
        unset pass REPLY_PASS 2>/dev/null || true
        return 0
    fi

    alias="${name//_/-}-screen"
    skip_blank=0
    blank_path="/sys/class/graphics/fb0/blank"
    if [[ "$blank" == /* ]]; then
        blank_path="$blank"
        skip_blank=0
    else
        skip_blank=1
        info "blank_path is not sysfs (${blank:-unset}) — installing SSH + shutdown only"
    fi

    echo
    # Provision against the address that answers now; pass reserved IP so post-renew
    # steps (Chromium) can follow the panel when it moves to the desired address.
    info "Logging into ${user}@${ssh_target} and provisioning the panel…"
    if configure_touchscreen_panel "$user" "$ssh_target" "$alias" "$blank_path" "$skip_blank" "$pass" "$ip" "$name"; then
        # SCCS SSHs to the reserved IP in conf — ensure ~/.ssh/config maps it to sccs_screen.
        ensure_screen_ssh_config "$user" "$ip" "$alias" || true
        if [[ "$ip" != "$ssh_target" ]]; then
            ensure_screen_ssh_config "$user" "$ssh_target" "${alias}-live" || true
        fi
        ok "Screen ${name} — SSH control + Chromium UI ready"
    else
        warn "Could not finish SSH setup for ${name}"
        info "Config + DHCP reservation were saved. Enable SSH on the panel, then menu 8 → option 2."
        unset pass REPLY_PASS 2>/dev/null || true
        return 1
    fi
    unset pass REPLY_PASS 2>/dev/null || true
    return 0
}

# Ensure ~/.ssh/config has a Host block for a screen IP (no live connection needed).
# Args: user host alias
ensure_screen_ssh_config() {
    local screen_user="$1" screen_host="$2" screen_alias="$3"
    run_user_bash env \
        SCREEN_USER="$screen_user" \
        SCREEN_HOST="$screen_host" \
        SCREEN_ALIAS="$screen_alias" \
        <<'EOS'
set -euo pipefail
SSH_CONFIG="$HOME/.ssh/config"
KNOWN_HOSTS="$HOME/.sccs/screen_known_hosts"
mkdir -p "$HOME/.ssh" "$(dirname "$KNOWN_HOSTS")"
chmod 700 "$HOME/.ssh"
touch "$SSH_CONFIG"
chmod 600 "$SSH_CONFIG"
tmp="$(mktemp)"
awk -v h="$SCREEN_HOST" -v a="$SCREEN_ALIAS" '
    BEGIN { skip=0 }
    /^Host[ \t]/ {
        skip=0
        for (i=2; i<=NF; i++) if ($i==h || $i==a) skip=1
        if (skip) next
    }
    skip { next }
    { print }
' "$SSH_CONFIG" >"$tmp"
cat >>"$tmp" <<EOF

# Screen control — ${SCREEN_HOST} (config/sccs.conf [screens])
Host ${SCREEN_HOST} ${SCREEN_ALIAS}
    HostName ${SCREEN_HOST}
    User ${SCREEN_USER}
    IdentityFile ~/.ssh/sccs_screen
    IdentitiesOnly yes
    StrictHostKeyChecking accept-new
    UserKnownHostsFile ~/.sccs/screen_known_hosts
EOF
mv "$tmp" "$SSH_CONFIG"
chmod 600 "$SSH_CONFIG"
echo "SSH config updated for ${SCREEN_USER}@${SCREEN_HOST}"
EOS
}

# Re-run full setup for one screen already in conf (by internal name).
# Returns 0 on success, 1 on failure/abort.
setup_existing_screens_ssh_one() {
    local want_name="$1"
    local row name host user blank mac friendly
    local pass="" alias skip_blank blank_path reserved bright icon day evening night reed
    local value_line ssh_target old_host live_ip
    local lan_if_tmp

    row="$(list_configured_screens | awk -F'\t' -v n="$want_name" '$1==n {print; exit}')"
    if [[ -z "$row" ]]; then
        fail "No [screens] entry named ${want_name}"
        return 1
    fi
    IFS=$'\t' read -r name host user blank mac friendly <<<"$row"
    old_host="$host"
    alias="${name//_/-}-screen"

    echo
    section_title "Re-setup: ${name}"
    info "This redoes SSH keys, display paths, sudoers, DHCP reservation, and renew."
    info "Config user=${user}  reserved IP=${host}  mac=${mac:--}"

    # Find where the panel is right now (for SSH) — may differ from reserved IP
    live_ip=""
    if panel_ssh_port_open "$old_host" 2; then
        live_ip="$old_host"
    fi
    if [[ -z "$live_ip" && -n "$mac" && "$mac" != "-" ]]; then
        lan_if_tmp="$(resolve_lan_if 2>/dev/null || echo eth0)"
        live_ip="$(ip neigh show dev "$lan_if_tmp" 2>/dev/null \
            | awk -v m="${mac,,}" 'tolower($0) ~ m && /lladdr/ {print $1; exit}')"
        if [[ -z "$live_ip" && -r /etc/pihole/dhcp.leases ]]; then
            live_ip="$(awk -v m="${mac,,}" 'tolower($2)==m {print $3; exit}' /etc/pihole/dhcp.leases)"
        fi
        if [[ -n "$live_ip" ]] && ! panel_ssh_port_open "$live_ip" 2; then
            # lease/ARP known but :22 closed — still try it for SSH later
            :
        fi
    fi
    if [[ -n "$live_ip" ]]; then
        info "Live address seen for this panel: ${live_ip}"
    else
        info "No live address found yet — will SSH to whatever you set as reserved IP"
        live_ip="$old_host"
    fi

    # ---- Choose reserved (static) IP — this is the main IP change step ----
    echo
    info "Set the ${C_BOLD}reserved IP${C_RESET} for this screen (written to sccs.conf + Pi-hole DHCP)."
    info "Current reserved IP: ${C_BOLD}${old_host}${C_RESET}"
    [[ "$live_ip" != "$old_host" ]] && info "Panel is currently at: ${C_BOLD}${live_ip}${C_RESET}"
    info "Enter a new address to move it, or press Enter to keep ${old_host}."
    info "Host Pi ${LAN_ADDR} (and this machine's other LAN IPs) cannot be reserved."
    prompt_reserved_lan_ip "$old_host" "$name"
    host="$REPLY_LAN_IP"
    if [[ "$host" != "$old_host" ]]; then
        ok "Will change reserved IP: ${old_host} → ${host}"
        info "Pi-hole will bind MAC ${mac:--} to ${host}; panel renews DHCP after setup."
    else
        info "Keeping reserved IP ${host}"
    fi

    # Prefer the new reserved (desired) IP when it already answers — the panel
    # may already have moved there. Fall back to the live lease, then the
    # previous reserved address.
    if ssh_target="$(resolve_panel_ssh_target "$host" "$live_ip" "$old_host")"; then
        if [[ "$ssh_target" == "$host" && "$host" != "$live_ip" ]]; then
            info "SSH target for this session: ${ssh_target} (reserved/desired IP)"
        elif [[ "$ssh_target" != "$host" ]]; then
            info "SSH target for this session: ${ssh_target} (live; reserved ${host} after renew)"
        else
            info "SSH target for this session: ${ssh_target}"
        fi
    else
        ssh_target="${ssh_target:-$host}"
        info "SSH target for this session: ${ssh_target} (no :22 open yet — will retry at login)"
    fi

    pass=""
    if load_screen_password "$name" "$ssh_target" "$user"; then
        pass="$REPLY_PASS"
    elif [[ "$host" != "$ssh_target" ]] && load_screen_password "$name" "$host" "$user"; then
        pass="$REPLY_PASS"
    fi
    REPLY_PASS=""
    if [[ -n "$pass" ]]; then
        info "Using the saved panel password for ${name}"
        [[ -n "${REPLY_CRED_USER:-}" ]] && user="$REPLY_CRED_USER"
        if ! verify_panel_ssh "$user" "$ssh_target" "$pass"; then
            warn "Saved panel password was not accepted for ${name}"
            forget_screen_credentials "$name"
            pass=""
        fi
    fi
    if [[ -z "$pass" ]]; then
        prompt_panel_ssh_credentials "$user" "${ssh_target}"
        user="$REPLY_USER"
        pass="$REPLY_PASS"
        if ! verify_panel_ssh "$user" "$ssh_target" "$pass"; then
            fail "Screen ${name} aborted — SSH login cancelled or failed"
            unset pass REPLY_PASS 2>/dev/null || true
            return 1
        fi
        user="$REPLY_USER"
        pass="$REPLY_PASS"
    fi

    bright="/sys/class/graphics/fb0/blank"
    blank="/sys/class/graphics/fb0/blank"
    if probe_panel_display_controls "$user" "$ssh_target" "$pass"; then
        bright="${REPLY_BRIGHT:-$bright}"
        blank="${REPLY_BLANK:-$blank}"
        info "Display: ${REPLY_METHOD:-ok}  bright=${bright}  blank=${blank}"
    else
        info "No display controls detected — using framebuffer defaults"
    fi

    # Load remaining fields from conf (do not clobber reserved $host)
    local old_name="$name"
    reed=""; icon="fa-display"; day=100; evening=30; night=5
    mapfile -t _scr < <(python3 - "$CONF" "$name" <<'PY'
import configparser, sys
cfg = configparser.ConfigParser()
cfg.read(sys.argv[1])
name = sys.argv[2]
line = cfg.get("screens", name, fallback="")
parts = [p.strip() for p in line.split("|")]
while len(parts) < 11:
    parts.append("")
print("\t".join(parts[:11]))
PY
)
    if [[ ${#_scr[@]} -gt 0 && -n "${_scr[0]:-}" ]]; then
        local _f _h _u _b _bl _mac
        IFS=$'\t' read -r _f reed _h _u _b icon day evening night _bl _mac <<<"${_scr[0]}"
        friendly="${friendly:-$_f}"
        icon="${icon:-fa-display}"
        day="${day:-100}"
        evening="${evening:-30}"
        night="${night:-5}"
        if [[ -n "${_mac:-}" && "$_mac" != "-" ]]; then
            mac="$_mac"
        fi
    fi
    if [[ -z "$mac" || "$mac" == "-" ]]; then
        mac="$(ip neigh show "$ssh_target" 2>/dev/null | awk '/lladdr/ {print tolower($5); exit}')"
        mac="${mac:--}"
    fi

    # ---- Identity: internal name, friendly name, reed, icon ----
    echo
    info "Screen identity (Enter keeps current value):"
    local new_name ans
    while true; do
        read -r -p "  Internal name (config key) [${name}]: " new_name || true
        new_name="${new_name:-$name}"
        new_name="$(printf '%s' "$new_name" | tr '[:upper:]' '[:lower:]' | sed -E 's/[^a-z0-9_]+/_/g; s/^_+//; s/_+$//')"
        if [[ "$new_name" =~ ^[a-z][a-z0-9_]*$ ]]; then
            # Conflict if renaming onto an existing different key
            if [[ "$new_name" != "$old_name" ]] && list_configured_screens | awk -F'\t' -v n="$new_name" '$1==n {found=1} END{exit !found}'; then
                warn "Internal name '${new_name}' already exists — pick another"
                continue
            fi
            name="$new_name"
            break
        fi
        warn "Use lowercase letters, digits, underscore (e.g. kitchen)"
    done
    if [[ "$name" != "$old_name" ]]; then
        ok "Will rename config key: ${old_name} → ${name}"
    fi

    read -r -p "  Friendly name [${friendly:-$name}]: " ans || true
    friendly="${ans:-${friendly:-$name}}"

    pick_screen_reed "$reed"
    reed="${REPLY_REED:-}"

    # Default icon from reed if still generic
    if [[ -z "$icon" || "$icon" == "fa-display" ]]; then
        if [[ "$reed" == *kitchen* ]]; then icon="fa-utensils"
        elif [[ "$reed" == *storage* ]]; then icon="fa-boxes-stacked"
        else icon="fa-display"
        fi
    fi
    read -r -p "  Icon (Font Awesome class) [${icon}]: " ans || true
    icon="${ans:-$icon}"
    [[ -n "$icon" ]] || icon="fa-display"

    # host = new reserved IP (must appear in conf + Pi-hole)
    value_line="${friendly} | ${reed} | ${host} | ${user} | ${bright} | ${icon} | ${day} | ${evening} | ${night} | ${blank} | ${mac}"
    info "Writing [screens] ${name} with reserved IP ${C_BOLD}${host}${C_RESET}"
    echo "  ${C_DIM}${name} = ${value_line}${C_RESET}"
    conf_upsert_screen "$name" "$value_line"
    if [[ "$name" != "$old_name" ]]; then
        conf_remove_screen "$old_name"
        ok "Renamed [screens] ${old_name} → ${name}"
    fi
    ok "Config updated (host=${host})"
    alias="${name//_/-}-screen"

    if command -v pihole >/dev/null 2>&1 || systemctl is-active --quiet pihole-FTL 2>/dev/null; then
        info "Applying Pi-hole DHCP reservation ${mac} → ${host}…"
        apply_pihole_screen_reservations "$CONF"
    else
        warn "Pi-hole not active — reservation not applied (start networking / menu 6)"
    fi

    skip_blank=0
    blank_path="/sys/class/graphics/fb0/blank"
    if [[ "$blank" == /* ]]; then
        blank_path="$blank"
        skip_blank=0
    else
        skip_blank=1
        info "blank_path is not sysfs (${blank:-unset}) — SSH + shutdown sudoers only"
    fi

    if [[ "$name" != "$old_name" ]]; then
        forget_screen_credentials "$old_name"
    fi
    save_screen_credentials "$name" "$host" "$user" "$pass" || true

    info "Provisioning passwordless SSH, sudo, desktop login, and DHCP renew on ${user}@${ssh_target}…"
    if configure_touchscreen_panel "$user" "$ssh_target" "$alias" "$blank_path" "$skip_blank" "$pass" "$host" "$name"; then
        # Always ensure SSH config covers the reserved IP (SCCS connects to conf host)
        ensure_screen_ssh_config "$user" "$host" "$alias" || true
        if [[ "$ssh_target" != "$host" ]]; then
            ensure_screen_ssh_config "$user" "$ssh_target" "${alias}-live" || true
        fi
        ok "Screen ${name} re-setup complete"
        if [[ "$host" != "$old_host" ]]; then
            ok "Reserved IP is now ${host} (was ${old_host})"
        fi
        unset pass REPLY_PASS 2>/dev/null || true
        return 0
    fi
    fail "Screen ${name} NOT fully configured (remote setup failed after login)"
    info "Config may already have host=${host}; fix SSH and re-run option 2."
    unset pass REPLY_PASS 2>/dev/null || true
    return 1
}

# Re-run full setup for screens already in conf (SSH, display probe, DHCP, keys).
# Returns 0 only if every selected screen provisioned successfully.
setup_existing_screens_ssh() {
    local -a SCREEN_ROWS=()
    local row name host user blank mac friendly i ans
    local any_ok=0 any_fail=0
    mapfile -t SCREEN_ROWS < <(list_configured_screens)
    if [[ ${#SCREEN_ROWS[@]} -eq 0 ]]; then
        warn "No screens in [screens] yet — use option 1 to add one from a LAN scan"
        return 1
    fi

    echo
    section_title "Re-run setup for existing screen"
    info "This redoes SSH keys, display paths, sudoers, and DHCP reservation (like first-time setup)."
    echo
    info "Configured screens:"
    for i in "${!SCREEN_ROWS[@]}"; do
        row="${SCREEN_ROWS[$i]}"
        IFS=$'\t' read -r name host user blank mac friendly <<<"$row"
        printf "  ${C_BOLD}[%d]${C_RESET}  %s  →  %s@%s  mac=%s  (%s)\n" \
            "$((i + 1))" "$name" "$user" "$host" "${mac:--}" "${friendly:-$name}"
    done
    echo
    info "Pick a number, ${C_BOLD}a${C_RESET} = all, or ${C_BOLD}s${C_RESET} = skip."
    read -r -p "  Screen [1]: " ans || true
    ans="${ans:-1}"
    case "${ans,,}" in
        s|skip|n|no) skip_note "Existing screen re-setup skipped"; return 0 ;;
    esac

    local -a targets=()
    if [[ "${ans,,}" == "a" || "${ans,,}" == "all" ]]; then
        targets=("${SCREEN_ROWS[@]}")
    elif [[ "$ans" =~ ^[0-9]+$ ]] && (( ans >= 1 && ans <= ${#SCREEN_ROWS[@]} )); then
        targets=("${SCREEN_ROWS[$((ans - 1))]}")
    else
        warn "Invalid choice"
        return 1
    fi

    for row in "${targets[@]}"; do
        IFS=$'\t' read -r name host user blank mac friendly <<<"$row"
        if setup_existing_screens_ssh_one "$name"; then
            any_ok=1
        else
            any_fail=1
            break
        fi
    done

    if [[ "$any_fail" -ne 0 ]]; then
        warn "One or more screens failed to re-configure"
        return 1
    fi
    if [[ "$any_ok" -eq 0 ]]; then
        warn "No screens were re-configured"
        return 1
    fi
    return 0
}

# mode: optional | required
# Scan wired LAN, add a panel to [screens], install passwordless SSH and desktop login.
step_screens() {
    local mode="${1:-optional}"
    local lan_if
    local -a CLIENTS=() SCREEN_ROWS=()
    local i row ip mac host src choice ans already

    step_begin "Touchscreens (scan LAN · config · SSH · desktop login · Chromium UI)"
    require_conf
    # One restart after the last panel in this step, including early returns.
    SCREEN_CONFIG_CHANGED=0
    trap 'trap - RETURN; restart_sccs_if_screen_config_changed' RETURN

    if [[ "$mode" == "optional" ]]; then
        ask_yn "Configure touchscreen panel(s) now?" y
        if [[ "$REPLY" != "y" ]]; then
            skip_note "Touchscreen setup skipped — menu item later when panels are online"
            return 0
        fi
    fi

    # Every panel already in [screens], including Armbian KDE Plasma, gets
    # desktop login, the Chromium homepage, and idle-power off. Adding or
    # re-running one screen below does the same again for that panel.
    apply_all_configured_touchscreen_desktop

    mapfile -t SCREEN_ROWS < <(list_configured_screens)
    if [[ ${#SCREEN_ROWS[@]} -gt 0 ]]; then
        echo
        info "Already in sccs.conf [screens]:"
        for i in "${!SCREEN_ROWS[@]}"; do
            row="${SCREEN_ROWS[$i]}"
            IFS=$'\t' read -r name host user blank mac friendly <<<"$row"
            printf "  ${C_DIM}•${C_RESET} %s  →  %s@%s  mac=%s\n" \
                "$name" "$user" "$host" "${mac:--}"
        done
    else
        info "No touchscreens in [screens] yet"
    fi

    echo
    echo "  ${C_BOLD}What next?${C_RESET}"
    echo "  ${C_CYAN}1${C_RESET}  Scan wired LAN and ${C_BOLD}add a new${C_RESET} touchscreen"
    echo "  ${C_CYAN}2${C_RESET}  ${C_BOLD}Re-run setup${C_RESET} for an existing screen (IP · SSH · paths · DHCP · keys)"
    if [[ ${#SCREEN_ROWS[@]} -eq 0 ]]; then
        echo "     ${C_DIM}(option 2 needs an entry in [screens] first)${C_RESET}"
    fi
    echo "  ${C_CYAN}q${C_RESET}  Done"
    echo
    # Default: re-run if screens already exist, else scan to add
    local def_choice=1
    [[ ${#SCREEN_ROWS[@]} -gt 0 ]] && def_choice=2
    read -r -p "  ${C_BOLD}Select${C_RESET} [${def_choice}]: " choice || true
    choice="${choice:-$def_choice}"

    case "${choice,,}" in
        2)
            if setup_existing_screens_ssh; then
                ok "Touchscreen step finished"
                return 0
            fi
            warn "Touchscreen step finished with errors — fix SSH and try again (menu stays open)"
            return 1
            ;;
        q|quit|s|skip|n|no)
            skip_note "Touchscreen setup finished without changes"
            return 0
            ;;
        1|"")
            ;;
        *)
            warn "Unknown option — scanning LAN"
            ;;
    esac

    if ! lan_if="$(resolve_lan_if)"; then
        die "No LAN ethernet interface found — configure networking (menu 6) first"
    fi
    if ! ip -4 addr show dev "$lan_if" 2>/dev/null | grep -q "inet "; then
        die "No IPv4 on ${lan_if} — run menu 6 (LAN / Pi-hole) first"
    fi

    # First scan after DHCP came up. A panel that was already on never asked
    # this server for an address, so the sweep will not see it.
    if [[ -f "$DHCP_PANEL_RESTART_FLAG" ]]; then
        echo
        info "Pi-hole DHCP just started handing out addresses on the van LAN."
        info "Restart every touchscreen (power off, then on) and wait until each one has finished booting."
        ask_yn "Touchscreens restarted. Start detection?" y
        if [[ "$REPLY" != "y" ]]; then
            skip_note "Touchscreen detection skipped — restart the panels, then run menu 8"
            return 0
        fi
        rm -f "$DHCP_PANEL_RESTART_FLAG"
    fi

    while true; do
        echo
        section_title "Linux / Armbian panels (${lan_if})"
        # Capture TSV only; drop anything that is not a real client row.
        # (status text must never be listed — progress goes to stderr inside the scan)
        local -a CLIENTS_RAW=() CLIENTS=()
        mapfile -t CLIENTS_RAW < <(scan_wired_lan_clients "$lan_if" "$LAN_ADDR")
        CLIENTS=()
        for row in "${CLIENTS_RAW[@]+"${CLIENTS_RAW[@]}"}"; do
            # Strict: must start with IPv4 then a tab (TSV from scan). Drops status noise.
            [[ "$row" =~ ^[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}$'\t' ]] || continue
            CLIENTS+=("$row")
        done

        if [[ ${#CLIENTS[@]} -eq 0 ]]; then
            warn "No Linux or Armbian panels found on ${lan_if}"
            info "The panel must be on this LAN with SSH enabled. Phones and other clients are skipped."
            ask_yn "Scan again?" y
            [[ "$REPLY" == "y" ]] || break
            continue
        fi

        # Preload configured IPs/MACs for annotation
        # (bash rejects empty keys on associative arrays — always guard subscripts)
        unset SCREEN_BY_IP SCREEN_BY_MAC 2>/dev/null || true
        declare -A SCREEN_BY_IP=() SCREEN_BY_MAC=()
        mapfile -t SCREEN_ROWS < <(list_configured_screens)
        for row in "${SCREEN_ROWS[@]}"; do
            [[ -z "${row//[$'\t']/}" ]] && continue
            IFS=$'\t' read -r name host user blank smac friendly <<<"$row"
            [[ -n "${host:-}" ]] && SCREEN_BY_IP["$host"]="$name"
            smac="${smac,,}"
            if [[ -n "${smac:-}" && "$smac" != "-" ]]; then
                SCREEN_BY_MAC["$smac"]="$name"
            fi
        done

        echo
        printf "  ${C_BOLD}%-4s %-16s %-20s %-18s %s${C_RESET}\n" "#" "IP" "MAC" "HOSTNAME" "NOTES"
        hr
        local -a CLIENT_IPS=() CLIENT_MACS=() CLIENT_HOSTS=() CLIENT_SRCS=()
        local n_show=0
        for i in "${!CLIENTS[@]}"; do
            IFS=$'\t' read -r ip mac host src <<<"${CLIENTS[$i]}"
            ip="${ip// /}"; mac="${mac// /}"; host="${host:--}"; src="${src:-}"
            # Final guard — never list non-IPs (e.g. leaked status text)
            [[ "$ip" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || continue
            n_show=$((n_show + 1))
            CLIENT_IPS+=("$ip")
            CLIENT_MACS+=("$mac")
            CLIENT_HOSTS+=("$host")
            CLIENT_SRCS+=("$src")
            already=""
            if [[ -n "$ip" && -n "${SCREEN_BY_IP[$ip]+x}" ]]; then
                already="screen:${SCREEN_BY_IP[$ip]}"
            else
                mac_l="${mac,,}"
                if [[ -n "$mac_l" && "$mac_l" != "-" && -n "${SCREEN_BY_MAC[$mac_l]+x}" ]]; then
                    already="screen:${SCREEN_BY_MAC[$mac_l]}"
                fi
            fi
            notes="$src"
            [[ -n "$already" ]] && notes="${notes}  ${C_GREEN}${already}${C_RESET}"
            printf "  ${C_BOLD}[%-2d]${C_RESET} %-16s %-20s %-18s %b\n" \
                "$n_show" "$ip" "${mac:--}" "$host" "$notes"
        done
        if [[ "$n_show" -eq 0 ]]; then
            warn "Scan produced no valid IPv4 clients"
            ask_yn "Scan again?" y
            [[ "$REPLY" == "y" ]] || break
            continue
        fi
        echo
        info "Pick a client number, ${C_BOLD}m${C_RESET} = manual IP, ${C_BOLD}r${C_RESET} = rescan, ${C_BOLD}q${C_RESET} = done"
        info "Clients marked ${C_GREEN}screen:…${C_RESET} are already in config — choosing them re-runs setup."
        read -r -p "  Client: " ans || true
        case "${ans,,}" in
            ""|q|quit|s|skip)
                break
                ;;
            r|rescan)
                continue
                ;;
            m|manual)
                read -r -p "  IP address: " ip || true
                [[ -n "$ip" ]] || { warn "No IP entered"; continue; }
                ip="${ip// /}"
                if ! ip="$(validate_lan_ip "$ip" "$LAN_ADDR" 2>&1)"; then
                    warn "Invalid LAN IP: ${ip}"
                    continue
                fi
                mac="$(ip neigh show "$ip" 2>/dev/null | awk '/lladdr/ {print tolower($5); exit}')"
                mac="${mac:--}"
                host="-"
                # Try lease file for mac/hostname
                if [[ -r /etc/pihole/dhcp.leases ]]; then
                    row="$(awk -v ip="$ip" 'NF>=4 && $3==ip {print tolower($2)"\t"$4; exit}' /etc/pihole/dhcp.leases)"
                    if [[ -n "$row" ]]; then
                        mac="${row%%$'\t'*}"
                        host="${row#*$'\t'}"
                    fi
                fi
                if [[ -n "${SCREEN_BY_IP[$ip]+x}" ]]; then
                    info "Already configured as ${SCREEN_BY_IP[$ip]} — re-running setup"
                    setup_existing_screens_ssh_one "${SCREEN_BY_IP[$ip]}" || true
                else
                    if ! linux_panel_os "$ip" >/dev/null; then
                        warn "${ip} does not look like a Linux/Armbian panel (no Debian, Ubuntu, or Armbian OpenSSH banner)."
                        ask_yn "Set it up anyway?" n
                        [[ "$REPLY" == "y" ]] || continue
                    fi
                    define_touchscreen_from_client "$ip" "$mac" "$host" || true
                fi
                ;;
            *)
                if [[ "$ans" =~ ^[0-9]+$ ]] && (( ans >= 1 && ans <= n_show )); then
                    ip="${CLIENT_IPS[$((ans - 1))]}"
                    mac="${CLIENT_MACS[$((ans - 1))]}"
                    host="${CLIENT_HOSTS[$((ans - 1))]}"
                    mac_l="${mac,,}"
                    if [[ -n "${SCREEN_BY_IP[$ip]+x}" ]]; then
                        info "Already configured as ${SCREEN_BY_IP[$ip]} — re-running setup"
                        setup_existing_screens_ssh_one "${SCREEN_BY_IP[$ip]}" || true
                    elif [[ -n "$mac_l" && "$mac_l" != "-" && -n "${SCREEN_BY_MAC[$mac_l]+x}" ]]; then
                        info "Already configured as ${SCREEN_BY_MAC[$mac_l]} — re-running setup"
                        setup_existing_screens_ssh_one "${SCREEN_BY_MAC[$mac_l]}" || true
                    else
                        define_touchscreen_from_client "$ip" "$mac" "$host" || true
                    fi
                else
                    warn "Invalid choice"
                    continue
                fi
                ;;
        esac

        ask_yn "Add or set up another touchscreen?" n
        [[ "$REPLY" == "y" ]] || break
    done

    ok "Touchscreen step finished"
}

step_service() {
    step_begin "systemd service ${SERVICE_NAME}.service"
    require_checkout
    [[ -x "$SCCS_HOME/venv/bin/python3" ]] || die "venv missing — run Install SCCS first"

    local unit_etc="/etc/systemd/system/${SERVICE_NAME}.service"
    cat >"$unit_etc" <<EOF
[Unit]
Description=The Singularity Camper Control System
After=network.target nginx.service

[Service]
User=$USERNAME
Group=www-data
WorkingDirectory=$SCCS_HOME
ExecStart=$SCCS_HOME/venv/bin/python3 $SCCS_HOME/app.py
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
Environment=PYTHONUNBUFFERED=1
LimitNOFILE=65535

[Install]
WantedBy=multi-user.target
EOF
    cp -a "$unit_etc" "${SCCS_HOME}.service"
    chown "$USERNAME":www-data "${SCCS_HOME}.service" 2>/dev/null || true

    systemctl daemon-reload
    systemctl enable "${SERVICE_NAME}.service"
    systemctl restart "${SERVICE_NAME}.service"
    sleep 1
    if systemctl is-active --quiet "${SERVICE_NAME}.service"; then
        ok "${SERVICE_NAME}.service is active"
    else
        warn "Service failed — journalctl -u ${SERVICE_NAME}.service -b --no-pager"
        systemctl status "${SERVICE_NAME}.service" --no-pager -l || true
    fi
}

step_checklist() {
    step_begin "Notes"
    info "Touchscreens: menu 8 / --screens (LAN scan → config → SSH, desktop login, Chromium UI)"
    info "HomeKit or Google Home: menu 10 / --voice (then pair from the Settings tab)"
    info "Hardware you skipped: finish later from this menu"
    ok "Done"
}

# ---------------------------------------------------------------------------
# apt update + upgrade on a configured touchscreen.
# mode "install" only writes /usr/local/sbin/sccs-apt-upgrade and a NOPASSWD
# sudoers rule. mode "upgrade" installs that helper if needed, then runs it.
# Exit 0: done. Exit 2: sudo needs the panel password. Exit 10: upgrade
# finished and /var/run/reboot-required is present. Any other non-zero: failed.
# Args: user host [password] mode
# ---------------------------------------------------------------------------
touchscreen_apt() {
    local screen_user="$1" screen_host="$2" screen_pass="${3:-}" mode="${4:-upgrade}"
    [[ "$mode" == "install" || "$mode" == "upgrade" ]] || return 1
    [[ "$screen_user" =~ ^[A-Za-z_][-A-Za-z0-9_]*$ ]] || return 1
    [[ "$screen_host" =~ ^[A-Za-z0-9][-A-Za-z0-9._]*$ ]] || return 1

    run_user_bash env \
        SCREEN_USER="$screen_user" \
        SCREEN_HOST="$screen_host" \
        SCREEN_PASS="${screen_pass:-}" \
        SCREEN_APT_MODE="$mode" \
        <<'EOS'
set -euo pipefail
KEY="$HOME/.ssh/sccs_screen"
KNOWN_HOSTS="$HOME/.sccs/screen_known_hosts"
SSH_BATCH=(
    -o BatchMode=yes
    -o PreferredAuthentications=publickey
    -o IdentitiesOnly=yes
    -o UserKnownHostsFile="${KNOWN_HOSTS}"
    -o StrictHostKeyChecking=accept-new
    -o ConnectTimeout=15
    -o ServerAliveInterval=30
    -o ServerAliveCountMax=120
    -i "${KEY}"
)
[[ -f "$KEY" ]] || { echo "Missing SSH key $KEY" >&2; exit 1; }

if ! ssh "${SSH_BATCH[@]}" "${SCREEN_USER}@${SCREEN_HOST}" "echo sccs-ssh-ok" </dev/null 2>/dev/null \
    | tr -d '\r' | grep -qx 'sccs-ssh-ok'; then
    echo "SSH key login failed for ${SCREEN_USER}@${SCREEN_HOST}" >&2
    exit 1
fi

helper_ready() {
    ssh "${SSH_BATCH[@]}" "${SCREEN_USER}@${SCREEN_HOST}" \
        "test -x /usr/local/sbin/sccs-apt-upgrade && sudo -n -l /usr/local/sbin/sccs-apt-upgrade" \
        </dev/null >/dev/null 2>&1
}

# Root-side installer. $1 is the panel username. Written via base64 so the
# panel login shell does not have to be bash.
install_root=$(cat <<'EOF'
set -euo pipefail
user="$1"
[[ "$user" =~ ^[A-Za-z_][-A-Za-z0-9_]*$ ]] || { echo "refusing unsafe username" >&2; exit 1; }
install -d -m 755 /usr/local/sbin /etc/sudoers.d
helper="$(mktemp /usr/local/sbin/.sccs-apt-upgrade.XXXXXX)"
cat > "$helper" <<'SCRIPT'
#!/bin/bash
# Installed by the SCCS installer. Passwordless via /etc/sudoers.d/sccs-screen-apt.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
export NEEDRESTART_MODE=a
export NEEDRESTART_SUSPEND=1
export APT_LISTCHANGES_FRONTEND=none
export UCF_FORCE_CONFFOLD=1
if ! command -v apt-get >/dev/null 2>&1; then
    echo "apt-get not found" >&2
    exit 3
fi
dpkg --configure -a
apt-get update -y
apt-get -y \
    -o Dpkg::Options::=--force-confdef \
    -o Dpkg::Options::=--force-confold \
    upgrade
echo "Package upgrade finished"
SCRIPT
chmod 755 "$helper"
mv "$helper" /usr/local/sbin/sccs-apt-upgrade
rule="$(mktemp /etc/sudoers.d/.sccs-screen-apt.XXXXXX)"
printf '%s\n' "$user ALL=(root) NOPASSWD: /usr/local/sbin/sccs-apt-upgrade" > "$rule"
chmod 440 "$rule"
if ! visudo -cf "$rule"; then
    rm -f "$rule"
    exit 1
fi
mv "$rule" /etc/sudoers.d/sccs-screen-apt
chmod 440 /etc/sudoers.d/sccs-screen-apt
visudo -c
rm -f /tmp/sccs-apt-install.sh
echo "Installed /usr/local/sbin/sccs-apt-upgrade"
EOF
)
if ! b64="$(printf '%s' "$install_root" | base64 -w 0 2>/dev/null)"; then
    b64="$(printf '%s' "$install_root" | base64 | tr -d '\n')"
fi

install_helper() {
    local how="$1" rc=0 remote
    remote="command -v base64 >/dev/null 2>&1 || { echo 'base64 missing on panel' >&2; exit 1; }; umask 077; printf '%s' $(printf '%q' "$b64") | base64 -d > /tmp/sccs-apt-install.sh; chmod 700 /tmp/sccs-apt-install.sh; sudo -${how} -p '' bash /tmp/sccs-apt-install.sh $(printf '%q' "$SCREEN_USER"); rc=\$?; rm -f /tmp/sccs-apt-install.sh; exit \$rc"
    if [[ "$how" == "S" ]]; then
        printf '%s\n' "$SCREEN_PASS" | ssh "${SSH_BATCH[@]}" "${SCREEN_USER}@${SCREEN_HOST}" "$remote" || rc=$?
    else
        ssh "${SSH_BATCH[@]}" "${SCREEN_USER}@${SCREEN_HOST}" "$remote" </dev/null || rc=$?
    fi
    return "$rc"
}

if ! helper_ready; then
    if [[ -n "${SCREEN_PASS:-}" ]]; then
        if ! install_helper S; then
            if helper_ready; then
                exit 1
            fi
            exit 2
        fi
    elif ssh "${SSH_BATCH[@]}" "${SCREEN_USER}@${SCREEN_HOST}" "sudo -n true" </dev/null >/dev/null 2>&1; then
        if ! install_helper n; then
            exit 1
        fi
    else
        exit 2
    fi
fi

if ! helper_ready; then
    exit 1
fi

if [[ "$SCREEN_APT_MODE" == "install" ]]; then
    unset SCREEN_PASS
    exit 0
fi

# Map any helper failure to 1. Exit 2 is reserved for "sudo needs a password"
# (dpkg can also exit 2).
if ! ssh "${SSH_BATCH[@]}" "${SCREEN_USER}@${SCREEN_HOST}" \
    "sudo -n /usr/local/sbin/sccs-apt-upgrade" </dev/null; then
    unset SCREEN_PASS
    exit 1
fi
if ssh "${SSH_BATCH[@]}" "${SCREEN_USER}@${SCREEN_HOST}" \
    "test -f /var/run/reboot-required" </dev/null >/dev/null 2>&1; then
    unset SCREEN_PASS
    exit 10
fi
unset SCREEN_PASS
exit 0
EOS
}

# apt update + upgrade on every [screens] panel that accepts the SCCS SSH key.
# Unreachable panels are skipped. A panel without passwordless apt uses the
# password saved at setup. It asks only when nothing is saved, or the saved
# password is refused, and then stores the password that sudo accepted.
upgrade_configured_touchscreens() {
    local listing="" line name host user _blank _mac friendly label
    local rc=0 cached="" pass="" stored="" from_store=0
    local -a rows=()

    if [[ ! -f "$CONF" ]]; then
        info "No sccs.conf — skip touchscreen package updates"
        return 0
    fi
    if ! listing="$(list_configured_screens)"; then
        warn "Could not read [screens] — skip touchscreen package updates"
        return 0
    fi
    if [[ -z "$listing" ]]; then
        info "No touchscreens in [screens] — skip package updates"
        return 0
    fi

    mapfile -t rows < <(printf '%s\n' "$listing")
    if [[ ${#rows[@]} -eq 1 ]]; then
        info "apt update + upgrade on 1 configured touchscreen…"
    else
        info "apt update + upgrade on ${#rows[@]} configured touchscreens…"
    fi

    for line in "${rows[@]}"; do
        [[ -n "$line" ]] || continue
        IFS=$'\t' read -r name host user _blank _mac friendly <<<"$line"
        label="${friendly:-$name}"
        [[ -n "$label" ]] || label="$host"
        if [[ ! "$user" =~ ^[A-Za-z_][-A-Za-z0-9_]*$ ]] \
            || [[ ! "$host" =~ ^[A-Za-z0-9][-A-Za-z0-9._]*$ ]]; then
            warn "${label}: skipped package update — unexpected host or username in [screens]"
            skip_note "${label} package update skipped — check host and username in [screens]"
            continue
        fi
        if ! panel_ssh_port_open "$host" 3; then
            warn "${label} (${host}) is not reachable — skipped package update"
            skip_note "${label} package update skipped — panel not reachable"
            continue
        fi

        info "apt update + upgrade on ${label} (${user}@${host})…"
        stored=""
        from_store=0
        if load_screen_password "$name" "$host" "$user"; then
            stored="$REPLY_PASS"
            from_store=1
        fi
        REPLY_PASS=""
        rc=0
        if [[ -n "$stored" ]]; then
            info "Using the saved panel password for ${label}"
            touchscreen_apt "$user" "$host" "$stored" upgrade || rc=$?
        elif [[ -n "$cached" ]]; then
            touchscreen_apt "$user" "$host" "$cached" upgrade || rc=$?
        else
            touchscreen_apt "$user" "$host" "" upgrade || rc=$?
        fi

        if [[ "$rc" -eq 2 ]]; then
            if [[ "$from_store" -eq 1 ]]; then
                warn "Saved panel password was not accepted for ${label}"
                forget_screen_credentials "$name"
            elif [[ -n "$cached" ]]; then
                warn "Panel account password was not accepted for ${label}"
            fi
            ask_yn "Upgrade packages on ${label} (${user}@${host})? SSH is already using the key. The panel account password is required once, for sudo, to install passwordless package upgrades." y
            if [[ "$REPLY" != "y" ]]; then
                skip_note "${label} package update skipped"
                continue
            fi
            pass=""
            prompt_secret "Panel account password for ${user}@${host} (sudo, once): " pass
            if [[ -z "$pass" ]]; then
                skip_note "${label} package update skipped — no password"
                continue
            fi
            rc=0
            touchscreen_apt "$user" "$host" "$pass" upgrade || rc=$?
            # Exit 2 is a rejected password. Any other result means sudo
            # accepted it, so this panel and the next one can reuse it.
            if [[ "$rc" -ne 2 ]]; then
                save_screen_credentials "$name" "$host" "$user" "$pass" || true
                info "Saved the panel password for ${label}"
                cached="$pass"
            fi
            pass=""
        fi

        if [[ "$rc" -eq 0 || "$rc" -eq 10 ]]; then
            ok "Packages updated on ${label}"
            if [[ "$rc" -eq 10 ]]; then
                warn "Reboot ${label} to finish the package upgrade"
            fi
        elif [[ "$rc" -eq 2 ]]; then
            fail "Package update on ${label} needs the panel password (sudo refused)"
            skip_note "${label} package update skipped — sudo refused the password"
        else
            warn "Package update failed on ${label} (${user}@${host})"
            skip_note "${label} package update failed"
        fi
    done

    cached=""
    stored=""
    pass=""
    unset cached stored pass
    return 0
}

step_update() {
    step_begin "Update from repository"
    require_checkout
    [[ -d "$SCCS_HOME/.git" ]] || die "Not a git checkout — cannot update"

    ensure_git_remote

    if [[ ! -f "$CONF" ]]; then
        warn "No sccs.conf yet — after update, copy from sccs.conf.dist if this is a new tree"
    fi

    local before after dirty
    before="$(run_git_as_user -C "$SCCS_HOME" rev-parse --short HEAD 2>/dev/null || echo unknown)"
    info "Current revision: $before"

    dirty="$(run_git_as_user -C "$SCCS_HOME" status --porcelain 2>/dev/null || true)"
    if [[ -n "$dirty" ]]; then
        warn "Working tree has local changes:"
        run_git_as_user -C "$SCCS_HOME" status --short | sed 's/^/    /' || true
        ask_yn "Continue with git pull anyway (may fail if files conflict)?" n
        [[ "$REPLY" == "y" ]] || { skip_note "Update cancelled (dirty tree)"; return 0; }
    fi

    info "Fetching and pulling (fast-forward only)…"
    if ! run_as_user env GIT_TERMINAL_PROMPT=0 git -C "$SCCS_HOME" fetch --prune origin; then
        die "git fetch failed (check network access to GitHub)"
    fi
    if ! run_as_user env GIT_TERMINAL_PROMPT=0 git -C "$SCCS_HOME" pull --ff-only; then
        warn "git pull --ff-only failed (diverged history or local edits)"
        warn "Resolve manually in $SCCS_HOME, then re-run Update"
        upgrade_configured_touchscreens
        return 1
    fi

    after="$(run_git_as_user -C "$SCCS_HOME" rev-parse --short HEAD 2>/dev/null || echo unknown)"
    if [[ "$before" == "$after" ]]; then
        ok "Already up to date ($after)"
    else
        ok "Updated $before → $after"
        run_git_as_user -C "$SCCS_HOME" log --oneline "${before}..${after}" 2>/dev/null | head -15 | sed 's/^/    /' || true

        local esp_changed
        esp_changed="$(run_git_as_user -C "$SCCS_HOME" diff --name-only "$before" "$after" -- esp32/ 2>/dev/null || true)"
        if [[ -n "$esp_changed" ]]; then
            echo
            warn "New ESP32 firmware pulled from repository:"
            echo "$esp_changed" | sed 's/^/    /'
            ask_sccs_core_flash "Flash updated ESP32 firmware now (Pi must be on the SCCS Core)?" y
            if [[ "$REPLY" == "y" ]]; then
                step_esp required
            else
                skip_note "ESP32 firmware updated but not flashed — run: sudo $0 --esp"
            fi
        fi
    fi

    if [[ -x "$SCCS_HOME/venv/bin/pip" ]]; then
        info "Refreshing Python dependencies to latest…"
        run_as_user "$SCCS_HOME/venv/bin/pip" install --upgrade pip
        run_as_user "$SCCS_HOME/venv/bin/pip" install --upgrade --upgrade-strategy eager -r "$SCCS_HOME/requirements.txt"
        ok "requirements.txt installed (latest)"
    else
        warn "No venv — skip pip (run Install SCCS first)"
    fi
    local matter_on
    matter_on="$(conf_get matter enabled)"
    if [[ "${matter_on,,}" == "true" ]]; then
        VOICE_AVAHI_READY=0
        install_google_packages refresh
    fi

    # Pi-hole (if installed via LAN setup) — Core / Web / FTL
    if command -v pihole >/dev/null 2>&1; then
        info "Updating Pi-hole (Core, Web, FTL)…"
        if pihole -up; then
            ok "Pi-hole is up to date"
            # Gravity blocklists are separate from -up; refresh when online.
            if pihole -g; then
                ok "Pi-hole gravity lists refreshed"
            else
                warn "pihole -g failed (blocklists not refreshed — check network)"
            fi
        else
            warn "pihole -up failed — SCCS update continued; fix with: sudo pihole -up"
        fi
    else
        info "Pi-hole not installed — skip (install via menu 6 / --lan when needed)"
    fi

    upgrade_configured_touchscreens

    # Same ownership + permission sweep step_groups_and_dirs does on a fresh
    # install — chown -R alone fixes ownership on files git pull adds, but
    # not their mode bits, so new content can land group-read-only (644/755)
    # instead of group-writable (664/2775). Re-run every update so nothing
    # pulled later silently ends up unwritable.
    chown -R "$USERNAME":www-data "$SCCS_HOME"
    find "$SCCS_HOME" -type d -exec chmod 2775 {} +
    find "$SCCS_HOME" -type f -exec chmod ug+rw {} + 2>/dev/null || true
    [[ -f "$SCCS_HOME/install.sh" ]] && chmod ug+x "$SCCS_HOME/install.sh" || true

    if systemctl list-unit-files "${SERVICE_NAME}.service" &>/dev/null \
        && systemctl is-enabled --quiet "${SERVICE_NAME}.service" 2>/dev/null; then
        ask_yn "Restart ${SERVICE_NAME}.service to load the update?" y
        if [[ "$REPLY" == "y" ]]; then
            systemctl restart "${SERVICE_NAME}.service"
            sleep 1
            if systemctl is-active --quiet "${SERVICE_NAME}.service"; then
                ok "Service restarted"
            else
                warn "Service failed after restart — journalctl -u ${SERVICE_NAME}.service -b"
            fi
        fi
    fi
}

finish_summary() {
    section_title "Summary"
    echo "  ${C_DIM}App${C_RESET}      $SCCS_HOME"
    echo "  ${C_DIM}Config${C_RESET}   $CONF"
    echo "  ${C_DIM}UI${C_RESET}       http://${LAN_ADDR}/"
    echo "  ${C_DIM}Service${C_RESET}  systemctl status ${SERVICE_NAME}"
    echo "  ${C_DIM}Logs${C_RESET}     journalctl -u ${SERVICE_NAME}.service -f"
    echo "  ${C_DIM}Samba${C_RESET}    \\\\${LAN_ADDR}\\sccs  (${USERNAME})"
    echo

    if [[ ${#SKIPPED_NOTES[@]} -gt 0 ]]; then
        echo "  ${C_YELLOW}${C_BOLD}Skipped this session${C_RESET}"
        local note
        for note in "${SKIPPED_NOTES[@]}"; do
            echo "    ${C_DIM}·${C_RESET} $note"
        done
        echo "  ${C_DIM}Re-open this menu to finish when hardware is ready.${C_RESET}"
        echo
    fi

    if [[ "$GROUPS_CHANGED" -eq 1 ]]; then
        echo "  ${C_YELLOW}${C_BOLD}Groups updated${C_RESET} for $USERNAME (${REQUIRED_GROUPS[*]})"
        echo "  ${C_DIM}Service already has them after restart; SSH needs re-login or reboot.${C_RESET}"
        echo
    fi

    if [[ "$NEED_REBOOT" -eq 1 ]]; then
        ask_yn "Reboot now?" n
        if [[ "$REPLY" == "y" ]]; then
            ok "Rebooting…"
            systemctl reboot
        elif [[ -e /dev/ttyAMA2 && -e /dev/ttyAMA3 ]]; then
            warn "Reboot later if Bluetooth or 1-Wire are not active yet"
        else
            warn "Reboot later if UART / Bluetooth / 1-Wire are not active yet"
        fi
    fi
    echo
    ok "Finished"
    echo
}

# ===========================================================================
# Orchestration
# ===========================================================================

# Runs an optional install step immediately (mode=required, no internal ask)
# when the user opted in during the upfront checklist below; otherwise prints
# the step header and a skip note, so step numbering and the end-of-run
# summary look the same either way.
# Args: do("y"/"n") title skip_message step_function
run_optional_step() {
    local do="$1" title="$2" skip_msg="$3" fn="$4"
    if [[ "$do" == "y" ]]; then
        "$fn" required
    else
        step_begin "$title"
        skip_note "$skip_msg"
    fi
}

run_install() {
    TOTAL_STEPS=16
    STEP=0
    SKIPPED_NOTES=()
    section_title "Install SCCS"

    if [[ "${SCCS_RESUME_PHASE:-}" == "after-uart" ]]; then
        info "Continuing after the restart that brings up the ESP serial ports."
        # packages, repo, config, and boot firmware already ran
        STEP=9
        if ! esp_uarts_ready; then
            activate_host_uarts || true
        fi
        if esp_uarts_ready; then
            ok "ESP serial ports are available"
        else
            UART_REBOOT_DECLINED=1
            warn "The ESP serial ports are still missing after the restart."
            info "The rest of the install will continue. Flash later with: sudo $0 --esp"
        fi
    else
        info "Full install for a new Pi. Hardware steps can be skipped and finished later."
        echo
        ask_yn "Start Install SCCS?" y
        [[ "$REPLY" == "y" ]] || return 0

        step_packages
        step_groups_and_dirs
        step_repo
        step_samba
        step_nginx
        step_venv
        step_config
        step_boot_firmware

        if ! esp_uarts_ready; then
            if ! offer_restart_for_uarts after-uart; then
                UART_REBOOT_DECLINED=1
                warn "ESP flashing needs a restart before /dev/ttyAMA2 and /dev/ttyAMA3 exist."
                info "The rest of the install will continue. Flash later with: sudo $0 --esp"
            fi
        fi
    fi

    # After the 1-Wire overlay is in the boot config (and loaded live). Asking
    # earlier finds an empty bus and skips the assignment prompts.
    step_sensors optional

    echo
    section_title "Optional hardware setup"
    info "Answer once for each — anything skipped can be finished later from the main menu."
    echo
    local do_esp do_victron do_lan do_tether do_screens do_voice voice_choice
    ask_sccs_core_flash "Is this Pi connected to the SCCS Core (flash both ESP32s now)?" y
    do_esp="$REPLY"
    ask_yn "Configure Victron SmartShunt / MPPT now?" y
    do_victron="$REPLY"
    ask_yn "Configure LAN gateway + Pi-hole (DNS/DHCP) now?" y
    do_lan="$REPLY"
    ask_yn "Set up phone USB tethering as internet uplink now?" y
    do_tether="$REPLY"
    ask_yn "Configure touchscreen panel(s) now?" y
    do_screens="$REPLY"
    ask_yn "Install Apple HomeKit / Siri or Google Home / Gemini?" y
    do_voice="$REPLY"
    voice_choice=""
    if [[ "$do_voice" == "y" ]]; then
        ask_voice_product "$(voice_product_default)"
        voice_choice="$REPLY"
    fi
    echo

    run_optional_step "$do_esp" "ESP32 firmware" \
        "ESP32 flash skipped — re-run when the Pi is on the SCCS Core" step_esp
    run_optional_step "$do_victron" "Victron Equipment" \
        "Victron skipped — use menu item when you have MAC+keys" step_victron
    run_optional_step "$do_lan" "LAN gateway · Pi-hole DHCP/DNS · NAT" \
        "LAN gateway / Pi-hole skipped" step_lan
    run_optional_step "$do_tether" "Configure iPhone USB Hotspot" \
        "USB tether skipped — use menu later when a phone is available" step_usb_tether
    run_optional_step "$do_screens" "Touchscreens (scan LAN · config · SSH · desktop login · Chromium UI)" \
        "Touchscreen setup skipped — menu item later when panels are online" step_screens
    if [[ "$do_voice" == "y" ]]; then
        step_voice_assistants required "$voice_choice"
    else
        step_begin "HomeKit or Google Home"
        skip_note "HomeKit or Google Home skipped — use menu 10 later"
    fi

    step_service
    step_checklist
    finish_summary
    clear_install_resume
}

run_partial() {
    local title="$1"
    shift
    local rc=0
    TOTAL_STEPS=0
    STEP=0
    SKIPPED_NOTES=()
    section_title "$title"
    # Capture failures — do not let set -e abort the interactive menu mid-step.
    "$@" || rc=$?
    finish_summary
    return "$rc"
}

usage() {
    cat <<EOF
${C_BOLD}SCCS setup${C_RESET}

  First-time (any directory — clones into ~/sccs):
    curl -fsSL https://raw.githubusercontent.com/joelgoudswaard/sccs/main/install.sh \\
      -o /tmp/sccs-install.sh && sudo bash /tmp/sccs-install.sh

  sudo $0                 Interactive menu
  sudo $0 --install       Install SCCS
  sudo $0 --update        Pull latest code + deps + Pi-hole + touchscreen apt
  sudo $0 --sensors       1-Wire temperature sensors
  sudo $0 --esp           Flash ESP32 firmware
  sudo $0 --victron       Victron Equipment
  sudo $0 --lan           LAN gateway + Pi-hole DHCP/DNS
  sudo $0 --usb-tether    Configure iPhone USB Hotspot
  sudo $0 --screens       Scan LAN · add touchscreen · SSH + desktop login + Chromium UI
  sudo $0 --service       Install / restart systemd service
  sudo $0 --voice         HomeKit / Siri, Google Home / Gemini, or both
  sudo $0 --help          This help

Environment: USERNAME  SCCS_HOME  LAN_ADDR  REPO_URL

  Installs to ~/sccs for the sudo user (override with SCCS_HOME).
  Clones/pulls the public HTTPS repo (https://github.com/joelgoudswaard/sccs.git).
  Override with REPO_URL if needed.
EOF
}

show_menu() {
    local choice
    while true; do
        logo
        show_context
        echo "  ${C_BOLD}Main menu${C_RESET}"
        hr
        echo "  ${C_CYAN}1${C_RESET}  Install SCCS"
        echo "  ${C_CYAN}2${C_RESET}  Update from repository"
        echo "  ${C_CYAN}3${C_RESET}  Configure 1-Wire temperature sensors"
        echo "  ${C_CYAN}4${C_RESET}  Configure ESP32 firmware"
        echo "  ${C_CYAN}5${C_RESET}  Configure Victron Equipment"
        echo "  ${C_CYAN}6${C_RESET}  Configure Networking"
        echo "  ${C_CYAN}7${C_RESET}  Configure iPhone USB Hotspot"
        echo "  ${C_CYAN}8${C_RESET}  Configure Touchscreens"
        echo "  ${C_CYAN}9${C_RESET}  Restart ${SERVICE_NAME} service"
        echo "  ${C_CYAN}10${C_RESET} Configure HomeKit or Google Home"
        echo "  ${C_CYAN}q${C_RESET}  Quit"
        echo
        if ! read_input "  ${C_BOLD}Select${C_RESET} [1-10 / q]: "; then
            die "No interactive terminal available for the menu"
        fi
        choice="${REPLY_LINE}"
        case "${choice,,}" in
            1)
                run_install || true
                pause_enter
                ;;
            2)
                run_partial "Update from repository" step_update || true
                pause_enter
                ;;
            3)
                run_partial "Configure 1-Wire temperature sensors" step_sensors required || true
                pause_enter
                ;;
            4)
                run_partial "Configure ESP32 firmware" step_esp required || true
                pause_enter
                ;;
            5)
                run_partial "Configure Victron Equipment" step_victron required || true
                pause_enter
                ;;
            6)
                run_partial "Configure Networking" step_lan required || true
                pause_enter
                ;;
            7)
                run_partial "Configure iPhone USB Hotspot" step_usb_tether required || true
                pause_enter
                ;;
            8)
                # Failure (e.g. bad SSH) must not drop out of the menu
                run_partial "Configure Touchscreens" step_screens required || true
                pause_enter
                ;;
            9)
                run_partial "Restart ${SERVICE_NAME} service" step_service || true
                pause_enter
                ;;
            10)
                run_partial "Configure HomeKit or Google Home" step_voice_assistants required || true
                pause_enter
                ;;
            # Empty Enter re-prompts — do not treat as quit (that kicked users out).
            q|quit|exit)
                echo
                info "Goodbye."
                echo
                exit 0
                ;;
            "")
                continue
                ;;
            *)
                warn "Unknown option: $choice"
                sleep 1
                ;;
        esac
        # reset counters between menu actions
        NEED_REBOOT=0
        GROUPS_CHANGED=0
        SKIPPED_NOTES=()
    done
}

# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------
case "${1:-}" in
    -h|--help|help)
        usage
        exit 0
        ;;
esac

require_root "$@"
load_install_resume || true
resolve_identity

# A restart was taken so the UART overlays could load. Continue that install
# instead of dropping the user back at the menu. Explicit flags (--update,
# --lan, …) still do what they say.
if [[ -n "${SCCS_RESUME_PHASE:-}" ]]; then
    case "${1:-}" in
        ""|--menu|menu|--install|install)
            logo
            show_context
            if [[ "$SCCS_RESUME_PHASE" == "esp" ]]; then
                info "Continuing ESP32 flash after the restart"
                run_partial "ESP32 firmware" step_esp required
            else
                run_install
            fi
            clear_install_resume
            exit 0
            ;;
    esac
fi

case "${1:-}" in
    --install|install) RUN_MODE=install ;;
    --update|update)   RUN_MODE=update ;;
    --sensors|sensors) RUN_MODE=sensors ;;
    --esp|esp)         RUN_MODE=esp ;;
    --victron|victron) RUN_MODE=victron ;;
    --lan|lan)         RUN_MODE=lan ;;
    --usb-tether|usb-tether|tether) RUN_MODE=usb_tether ;;
    --screens|screens) RUN_MODE=screens ;;
    --service|service) RUN_MODE=service ;;
    --voice|voice|--homekit|homekit|--google-home) RUN_MODE=voice ;;
    --menu|menu|"")    RUN_MODE=menu ;;
    *)
        fail "Unknown option: $1"
        usage
        exit 1
        ;;
esac

case "$RUN_MODE" in
    menu)    show_menu ;;
    install) logo; show_context; run_install ;;
    update)  logo; show_context; run_partial "Update from repository" step_update ;;
    sensors) logo; show_context; run_partial "1-Wire temperature sensors" step_sensors required ;;
    esp)     logo; show_context; run_partial "ESP32 firmware" step_esp required ;;
    victron) logo; show_context; run_partial "Victron Equipment" step_victron required ;;
    lan)     logo; show_context; run_partial "LAN gateway" step_lan required ;;
    usb_tether) logo; show_context; run_partial "Configure iPhone USB Hotspot" step_usb_tether required ;;
    screens) logo; show_context; run_partial "Touchscreens" step_screens required ;;
    service) logo; show_context; run_partial "Service" step_service ;;
    voice)   logo; show_context; run_partial "HomeKit or Google Home" step_voice_assistants required ;;
esac
