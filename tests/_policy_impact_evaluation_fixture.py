"""Disposable local evaluation: real PostgreSQL policy and usage; simulated IdP/provider."""
import json, threading, http.client
from contextlib import contextmanager
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from tests._console_fixtures import ConsoleHTTPTestCase, activate_member
from tests._session_fixtures import fixture_environment
from hormuz.policy_console import PolicyConsoleServer
from hormuz.policy_control import PolicyControlService
from hormuz.policy_document import PolicyDocument
from hormuz.policy_repository import PolicyAdministrator
from hormuz.server import GatewayServer

class Proxy(BaseHTTPRequestHandler):
    timeout=10
    def do_GET(self): self.forward()
    def do_POST(self): self.forward()
    def forward(self):
        port = self.server.console_port if self.path.startswith(('/console','/v1/admin/')) else self.server.gateway_port
        conn=http.client.HTTPConnection('127.0.0.1',port,timeout=15)
        body=self.rfile.read(int(self.headers.get('Content-Length','0')))
        conn.request(self.command,self.path,body,{k:v for k,v in self.headers.items() if k.lower() not in ('connection','transfer-encoding')})
        resp=conn.getresponse(); data=resp.read()
        self.send_response(resp.status)
        for k,v in resp.getheaders():
            if k.lower() not in ('connection','transfer-encoding','content-length'): self.send_header(k,v)
        self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data); conn.close()
    def log_message(self,*args): pass


@contextmanager
def policy_impact_evaluation(pg):
    """Real listeners/PG stores with disposable simulated identity and provider."""
    case=ConsoleHTTPTestCase(); servers=[]
    managed, env, _=pg._managed_config()
    try:
        case.setUp()
        proxy=ThreadingHTTPServer(('127.0.0.1',0),Proxy)
        public='http://127.0.0.1:'+str(proxy.server_port)
        config=replace(case.config, usage_storage=managed.usage_storage, policy_control=managed.policy_control,
            session_broker=replace(case.config.session_broker,public_base_url=public,policy_impact_enabled=True))
        controller=PolicyControlService(config,environ=env,organization_ids=case.directory.managed_organization_ids())
        root=PolicyAdministrator(organization_id='customer-a',authentication_kind='oidc',issuer=case.idp.origin,subject='admin-subject')
        controller._repository.bootstrap(organization_id='customer-a',caller=root,administrators=(root,))
        document=PolicyDocument.from_mapping({'schema_id':'hormuz.policy-document','schema_version':1,'organization_id':'customer-a',
           'policies':{'organization':{'max_output_tokens':32},'teams':{},'actors':{}},
           'egress_controls':{'openai':{'allow_background':False,'allow_response_storage':False},'secrets':{'mode':'off'}}},config=controller._validation)
        controller._repository.apply(organization_id='customer-a',caller=root,document=document)
        for organization in config.organization_ids:
            admin=replace(root,organization_id=organization)
            controller._repository.bootstrap(organization_id=organization,caller=admin,administrators=(admin,))
            controller._repository.apply(organization_id=organization,caller=admin,document=replace(document,organization_id=organization))
        runtime_env={**fixture_environment(),config.usage_storage.postgres_dsn_env:env[config.usage_storage.postgres_dsn_env]}
        gateway=GatewayServer(config,environ=runtime_env)
        console=PolicyConsoleServer(config,address=('127.0.0.1',0),environ={
           config.usage_storage.postgres_dsn_env:env[config.usage_storage.postgres_dsn_env],
           config.policy_control.postgres_control_dsn_env:env[config.policy_control.postgres_control_dsn_env]})
        proxy.gateway_port=gateway.server_port; proxy.console_port=console.server_port
        for s in (gateway,console,proxy):
            t=threading.Thread(target=s.serve_forever,kwargs={'poll_interval':.05},daemon=True);t.start();servers.append((s,t))
        case.idp.gateway_url=public
        case.gateway_url=public
        _, native=activate_member(gateway.session_broker.store,gateway.session_broker.directory,subject='eval-member',email='eval@example.test',name='Local Evaluator')
        def request():
            status,_,_=case.request('POST','/v1/responses',{'model':'safe-openai','input':'Synthetic local evaluation','max_output_tokens':30},{'Authorization':'Bearer '+native.access_token,'X-Hormuz-Client':'codex'})
            gateway.impact_recorder._queue.join()
            return status
        assert request() == 200
        info={'url':public+'/console','organization':'customer-a','gateway_port':gateway.server_port,'console_port':console.server_port,'policy':'real PostgreSQL','usage':'real PostgreSQL','provider_and_identity':'local simulators','real_provider_calls':0}
        yield case, controller, root, gateway, console, proxy, request, info
    finally:
        for server, thread in reversed(servers):
            server.shutdown(); server.server_close(); thread.join(5)
        case.doCleanups()
