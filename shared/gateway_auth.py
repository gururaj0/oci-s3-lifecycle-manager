"""Consume identity asserted by the dedicated gateway, never a caller's JWT.
The API listener is network-restricted to the gateway NSG. Uvicorn proxy headers
must remain disabled so the peer check uses the transport address.
"""
import ipaddress
from shared.auth import Auth
from shared.errors import Problem

class GatewayAuth:
    allowed=Auth.allowed

    def __init__(self,cfg):
        self.cfg=cfg
        self.networks=[ipaddress.ip_network(n) for n in cfg.gateway_trusted_networks]
        if not self.networks:raise ValueError('Gateway authentication requires trusted transport networks')

    def verify_request(self,request):
        try:
            peer=ipaddress.ip_address(request.client.host)
            if not any(peer in n for n in self.networks):raise ValueError('peer')
            values={}
            for claim in ('issuer','subject','scope'):
                headers=request.headers.getlist('x-lcm-'+claim)
                if len(headers)!=1 or not headers[0] or len(headers[0])>4096:raise ValueError('header')
                values[claim]=headers[0]
            if values['issuer']!=self.cfg.issuer:raise ValueError('issuer')
            if any(c in values['subject'] for c in '\r\n,'):raise ValueError('subject')
            return {'issuer':values['issuer'],'subject':values['subject'],
                    'scopes':values['scope'].split()}
        except (ValueError,TypeError,AttributeError):
            raise Problem(401,'GATEWAY_IDENTITY_REQUIRED') from None
