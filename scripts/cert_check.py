import argparse
from datetime import datetime,timezone
import socket
import ssl
from urllib.parse import urlparse
from physics_worksheet_mcp.config import Config

if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('--config',required=True); a=p.parse_args()
    host=urlparse(Config.load(a.config).base_url).hostname
    with socket.create_connection((host,443),timeout=10) as sock:
        with ssl.create_default_context().wrap_socket(sock,server_hostname=host) as tls: cert=tls.getpeercert()
    expiry=ssl.cert_time_to_seconds(cert['notAfter']); remaining=expiry-datetime.now(timezone.utc).timestamp()
    print('TLS certificate remaining hours:',round(remaining/3600,1)); raise SystemExit(1 if remaining<72*3600 else 0)
