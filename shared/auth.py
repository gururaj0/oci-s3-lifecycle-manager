import json
import os
import time
import uuid
from pathlib import Path
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
from shared.errors import Problem


def private_file(path: Path, value: bytes):
    """Atomic first-write; two worker startups must not choose different encryption keys."""
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return path.read_bytes()
    with os.fdopen(fd, 'wb') as f:
        f.write(value)
    return value

class Auth:
    def __init__(self, cfg):
        self.cfg = cfg
        cfg.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        if cfg.mode == 'mock':
            key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            self.private = private_file(cfg.state_dir / 'demo-signing.pem', key.private_bytes(
                serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
            self.public = serialization.load_pem_private_key(self.private, password=None).public_key()
            self.jwks = {'keys': [dict(json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.public)),
                                        kid='demo-v1', use='sig', alg='RS256')]}
        else:
            if not cfg.jwks_url.startswith('https://'):
                raise ValueError('JWKS must use HTTPS')
            self.jwk_client = jwt.PyJWKClient(cfg.jwks_url, cache_keys=True, lifespan=300, timeout=5)

    def mint_demo(self, subject=None):
        if self.cfg.mode != 'mock':
            raise Problem(404, 'NOT_FOUND')
        now = int(time.time())
        return jwt.encode({'iss': self.cfg.issuer, 'aud': self.cfg.audience,
                           'sub': subject or 'demo:' + str(uuid.uuid4()), 'iat': now,
                           'exp': now + 3600}, self.private, algorithm='RS256', headers={'kid': 'demo-v1'})

    def verify(self, authorization):
        if not authorization or not authorization.startswith('Bearer '):
            raise Problem(401, 'INVALID_IDENTITY')
        token = authorization[7:]
        try:
            key = self.public if self.cfg.mode == 'mock' else self.jwk_client.get_signing_key_from_jwt(token).key
            claims = jwt.decode(token, key, algorithms=['RS256'], issuer=self.cfg.issuer,
                                audience=self.cfg.audience,
                                options={'require': ['exp', 'iss', 'aud', 'sub', 'iat']})
            if not isinstance(claims['sub'], str) or not claims['sub']:
                raise ValueError('subject')
            return {'issuer': claims['iss'], 'subject': claims['sub']}
        except (jwt.PyJWTError, ValueError, TypeError):
            raise Problem(401, 'INVALID_IDENTITY') from None

    def allowed(self, owner):
        if self.cfg.mode == 'mock' and owner['issuer'] == self.cfg.issuer and owner['subject'].startswith('demo:'):
            return list(self.cfg.profiles)
        return self.cfg.identities.get(owner['issuer'], {}).get(owner['subject'], [])
