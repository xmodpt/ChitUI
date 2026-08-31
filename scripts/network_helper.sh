#!/bin/bash
# ============================================================================
# ChitUI — Privileged Network Helper
# ============================================================================
# The ONLY thing ChitUI is allowed to run as root (see setup_network_sudo.sh).
# Every argument is validated here rather than trusted from the web layer, so
# a bug or an injection in the Flask app can't turn into arbitrary root
# execution.
#
# Commands:
#   status  <iface>
#   dhcp    <iface>
#   static  <iface> <ip> <netmask> <gateway> [dns1] [dns2]
#   backend
#
# Exit codes: 0 ok, 1 bad usage/validation, 2 no supported backend, 3 apply failed
# ============================================================================

set -uo pipefail

die() { echo "ERROR: $*" >&2; exit "${2:-1}"; }

# ── Argument validation ──────────────────────────────────────────────────────

valid_iface() {
    # Linux interface names: alphanumerics, dot, dash, underscore, colon. Max 15.
    [[ "$1" =~ ^[A-Za-z0-9._:-]{1,15}$ ]] || return 1
    # Must actually exist on this machine
    [[ -e "/sys/class/net/${1%%:*}" ]] || return 1
    return 0
}

valid_ipv4() {
    local ip="$1" o
    [[ "$ip" =~ ^[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}$ ]] || return 1
    IFS='.' read -ra o <<< "$ip"
    for octet in "${o[@]}"; do
        [[ "$octet" -le 255 ]] || return 1
        # Reject leading zeros like 010 - they're ambiguous (octal)
        [[ "$octet" == "0" || "${octet:0:1}" != "0" ]] || return 1
    done
    return 0
}

netmask_to_prefix() {
    local mask="$1" o bits=0
    IFS='.' read -ra o <<< "$mask"
    for octet in "${o[@]}"; do
        case "$octet" in
            255) bits=$((bits + 8)) ;;
            254) bits=$((bits + 7)) ;;
            252) bits=$((bits + 6)) ;;
            248) bits=$((bits + 5)) ;;
            240) bits=$((bits + 4)) ;;
            224) bits=$((bits + 3)) ;;
            192) bits=$((bits + 2)) ;;
            128) bits=$((bits + 1)) ;;
            0)   ;;
            *)   return 1 ;;
        esac
    done
    echo "$bits"
}

prefix_to_netmask() {
    local prefix="$1" mask="" i octet
    for i in 0 1 2 3; do
        if   [[ $prefix -ge 8 ]]; then octet=255; prefix=$((prefix - 8))
        elif [[ $prefix -gt 0 ]]; then octet=$((256 - 2 ** (8 - prefix))); prefix=0
        else octet=0; fi
        mask+="${octet}"
        [[ $i -lt 3 ]] && mask+="."
    done
    echo "$mask"
}

# ── Backend detection ────────────────────────────────────────────────────────

detect_backend() {
    if command -v nmcli >/dev/null 2>&1 && systemctl is-active --quiet NetworkManager 2>/dev/null; then
        echo "networkmanager"
    elif systemctl is-active --quiet dhcpcd 2>/dev/null && [[ -f /etc/dhcpcd.conf ]]; then
        echo "dhcpcd"
    else
        echo "none"
    fi
}

nm_connection_for() {
    # Active connection name bound to this device
    nmcli -t -g GENERAL.CONNECTION device show "$1" 2>/dev/null | head -n1
}

# ── status ───────────────────────────────────────────────────────────────────

cmd_status() {
    local iface="$1"
    valid_iface "$iface" || die "invalid interface: $iface"

    local backend ip prefix netmask gateway dns mode
    backend=$(detect_backend)

    # Live address straight from the kernel - accurate regardless of backend
    local cidr
    cidr=$(ip -4 -o addr show dev "$iface" 2>/dev/null | awk '{print $4}' | head -n1)
    ip="${cidr%%/*}"
    prefix="${cidr##*/}"
    [[ -n "$prefix" && "$prefix" != "$cidr" ]] && netmask=$(prefix_to_netmask "$prefix") || netmask=""

    gateway=$(ip -4 route show default dev "$iface" 2>/dev/null | awk '{print $3}' | head -n1)

    # Resolvers: prefer resolvectl, fall back to resolv.conf
    if command -v resolvectl >/dev/null 2>&1; then
        dns=$(resolvectl dns "$iface" 2>/dev/null | sed 's/^.*: //' | tr ' ' '\n' | grep -E '^[0-9.]+$' | paste -sd, -)
    fi
    [[ -z "${dns:-}" ]] && dns=$(grep -E '^nameserver' /etc/resolv.conf 2>/dev/null | awk '{print $2}' | grep -E '^[0-9.]+$' | paste -sd, -)

    # Configured mode (what it will do on next boot), not just what's live now
    mode="dhcp"
    case "$backend" in
        networkmanager)
            local conn method
            conn=$(nm_connection_for "$iface")
            if [[ -n "$conn" ]]; then
                method=$(nmcli -t -g ipv4.method connection show "$conn" 2>/dev/null)
                [[ "$method" == "manual" ]] && mode="static"
            fi
            ;;
        dhcpcd)
            grep -qE "^[[:space:]]*interface[[:space:]]+${iface}[[:space:]]*$" /etc/dhcpcd.conf 2>/dev/null \
                && grep -qE "^[[:space:]]*static[[:space:]]+ip_address=" /etc/dhcpcd.conf 2>/dev/null \
                && mode="static"
            ;;
    esac

    # Machine-readable output for the Flask layer
    printf 'backend=%s\n' "$backend"
    printf 'interface=%s\n' "$iface"
    printf 'mode=%s\n' "$mode"
    printf 'ip=%s\n' "${ip:-}"
    printf 'prefix=%s\n' "${prefix:-}"
    printf 'netmask=%s\n' "${netmask:-}"
    printf 'gateway=%s\n' "${gateway:-}"
    printf 'dns=%s\n' "${dns:-}"
}

# ── dhcp ─────────────────────────────────────────────────────────────────────

cmd_dhcp() {
    local iface="$1"
    valid_iface "$iface" || die "invalid interface: $iface"

    local backend
    backend=$(detect_backend)
    case "$backend" in
        networkmanager)
            local conn
            conn=$(nm_connection_for "$iface")
            [[ -n "$conn" ]] || die "no active NetworkManager connection for $iface" 3
            nmcli connection modify "$conn" \
                ipv4.method auto \
                ipv4.addresses "" \
                ipv4.gateway "" \
                ipv4.dns "" || die "nmcli modify failed" 3
            nmcli connection up "$conn" >/dev/null 2>&1 || die "nmcli up failed" 3
            ;;
        dhcpcd)
            dhcpcd_strip_block "$iface"
            systemctl restart dhcpcd || die "dhcpcd restart failed" 3
            ;;
        *)
            die "no supported network backend (need NetworkManager or dhcpcd)" 2
            ;;
    esac
    echo "OK"
}

# ── static ───────────────────────────────────────────────────────────────────

cmd_static() {
    local iface="$1" ip="$2" netmask="$3" gateway="$4" dns1="${5:-}" dns2="${6:-}"

    valid_iface "$iface"  || die "invalid interface: $iface"
    valid_ipv4 "$ip"      || die "invalid ip: $ip"
    valid_ipv4 "$netmask" || die "invalid netmask: $netmask"
    valid_ipv4 "$gateway" || die "invalid gateway: $gateway"
    [[ -n "$dns1" ]] && { valid_ipv4 "$dns1" || die "invalid dns1: $dns1"; }
    [[ -n "$dns2" ]] && { valid_ipv4 "$dns2" || die "invalid dns2: $dns2"; }

    local prefix
    prefix=$(netmask_to_prefix "$netmask") || die "netmask is not contiguous: $netmask"
    [[ "$prefix" -ge 1 && "$prefix" -le 32 ]] || die "nonsensical prefix from netmask: $netmask"

    local dns_list=""
    [[ -n "$dns1" ]] && dns_list="$dns1"
    [[ -n "$dns2" ]] && dns_list="${dns_list:+$dns_list,}$dns2"

    local backend
    backend=$(detect_backend)
    case "$backend" in
        networkmanager)
            local conn
            conn=$(nm_connection_for "$iface")
            [[ -n "$conn" ]] || die "no active NetworkManager connection for $iface" 3
            nmcli connection modify "$conn" \
                ipv4.method manual \
                ipv4.addresses "${ip}/${prefix}" \
                ipv4.gateway "$gateway" \
                ipv4.dns "${dns_list:-$gateway}" || die "nmcli modify failed" 3
            # Backgrounded: bringing the connection up drops the caller's own
            # TCP session when the address changes, and we still want a clean exit.
            ( sleep 1; nmcli connection up "$conn" >/dev/null 2>&1 ) &
            ;;
        dhcpcd)
            dhcpcd_strip_block "$iface"
            {
                echo ""
                echo "# --- ChitUI managed block for ${iface} (do not edit by hand) ---"
                echo "interface ${iface}"
                echo "static ip_address=${ip}/${prefix}"
                echo "static routers=${gateway}"
                [[ -n "$dns_list" ]] && echo "static domain_name_servers=${dns_list//,/ }"
                echo "# --- end ChitUI managed block for ${iface} ---"
            } >> /etc/dhcpcd.conf
            ( sleep 1; systemctl restart dhcpcd >/dev/null 2>&1 ) &
            ;;
        *)
            die "no supported network backend (need NetworkManager or dhcpcd)" 2
            ;;
    esac
    echo "OK"
}

dhcpcd_strip_block() {
    local iface="$1"
    [[ -f /etc/dhcpcd.conf ]] || return 0
    cp -a /etc/dhcpcd.conf "/etc/dhcpcd.conf.chitui.bak"
    sed -i "/# --- ChitUI managed block for ${iface} ---/,/# --- end ChitUI managed block for ${iface} ---/d" \
        /etc/dhcpcd.conf
}

# ── dispatch ─────────────────────────────────────────────────────────────────

[[ $# -ge 1 ]] || die "usage: $0 {status|dhcp|static|backend} [args]"

case "$1" in
    backend) detect_backend ;;
    status)  [[ $# -eq 2 ]] || die "usage: $0 status <iface>";  cmd_status "$2" ;;
    dhcp)    [[ $# -eq 2 ]] || die "usage: $0 dhcp <iface>";    cmd_dhcp   "$2" ;;
    static)
        [[ $# -ge 5 && $# -le 7 ]] || die "usage: $0 static <iface> <ip> <netmask> <gateway> [dns1] [dns2]"
        cmd_static "$2" "$3" "$4" "$5" "${6:-}" "${7:-}"
        ;;
    *) die "unknown command: $1" ;;
esac
