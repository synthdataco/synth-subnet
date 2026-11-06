import netaddr
import requests

EXTERNAL_IP_SERVICES = (
    "https://checkip.amazonaws.com",
    "https://ifconfig.me/ip",
    "https://ident.me",
)


class ExternalIPNotFound(Exception):
    pass


def get_external_ip() -> str:
    """This machine's public IP, from the first service that answers with a
    valid address."""
    for url in EXTERNAL_IP_SERVICES:
        try:
            ip = requests.get(url, timeout=10).text.strip()
            netaddr.IPAddress(ip)
            return ip
        except (requests.RequestException, netaddr.AddrFormatError):
            continue
    raise ExternalIPNotFound(
        f"none of {', '.join(EXTERNAL_IP_SERVICES)} returned an IP"
    )


def ip_to_int(ip: str) -> int:
    return int(netaddr.IPAddress(ip))


def ip_version(ip: str) -> int:
    return netaddr.IPAddress(ip).version
