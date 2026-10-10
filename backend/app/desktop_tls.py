"""Verified loopback TLS for the Mac preview; never disables certificate checks."""
from dataclasses import dataclass
from pathlib import Path
import os
import ssl
import subprocess
import tempfile


class TLSSetupRequired(ValueError):
    """The user's explicit local certificate setup has not been completed."""


@dataclass(frozen=True)
class LocalTLS:
    cert: Path
    key: Path
    ca: Path

    def server_options(self):
        # Validate PEMs and the key pair before claiming the listening socket.
        try:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            context.load_cert_chain(str(self.cert), str(self.key))
            self.client_context()
        except (OSError, ssl.SSLError) as exc:
            raise TLSSetupRequired('Local HTTPS certificates are missing or invalid. Run Prepare Premiere on Mac, then reopen Video Analyzer.') from exc
        return {'ssl_certfile': str(self.cert), 'ssl_keyfile': str(self.key),
                'ssl_version': ssl.PROTOCOL_TLS_SERVER,
                'ssl_ciphers': 'ECDHE+AESGCM:ECDHE+CHACHA20'}

    def client_context(self):
        # Python's CA bundle need not mirror the Mac Keychain. Trust only the
        # public local CA supplied by setup, keeping chain and hostname checks.
        return ssl.create_default_context(cafile=str(self.ca))


def configured_tls(data_dir: Path, cert=None, key=None, ca=None):
    supplied = [cert, key, ca]
    if any(supplied) and not all(supplied):
        raise ValueError('Supply --tls-cert, --tls-key and --tls-ca together.')
    directory = data_dir / 'tls'
    result = LocalTLS(*(Path(value).expanduser().resolve() for value in supplied)) if all(supplied) else LocalTLS(
        directory / 'localhost.pem', directory / 'localhost-key.pem', directory / 'rootCA.pem')
    if not all(path.is_file() for path in (result.cert, result.key, result.ca)):
        raise TLSSetupRequired('Local HTTPS has not been set up. Run Prepare Premiere on Mac, then reopen Video Analyzer.')
    result.server_options()
    return result


def smoke_tls(stack):
    """A short-lived test certificate, isolated from user data and OS trust stores."""
    directory = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix='video-analyzer-tls-smoke-')))
    configuration = directory / 'openssl.cnf'
    configuration.write_text('''[req]
distinguished_name = dn
x509_extensions = extensions
prompt = no
[dn]
CN = localhost
[extensions]
subjectAltName = DNS:localhost,IP:127.0.0.1,IP:::1
basicConstraints = critical,CA:TRUE
keyUsage = critical,digitalSignature,keyEncipherment,keyCertSign
extendedKeyUsage = serverAuth
''', encoding='utf-8')
    cert, key = directory / 'localhost.pem', directory / 'localhost-key.pem'
    try:
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                        '-config', str(configuration), '-keyout', str(key), '-out', str(cert)],
                       check=True, capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError('OpenSSL is required for the isolated HTTPS smoke test.') from exc
    os.chmod(key, 0o600)
    result = LocalTLS(cert, key, cert)
    result.server_options()
    return result
