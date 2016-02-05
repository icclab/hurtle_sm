# Copyright 2014-2015 Zuercher Hochschule fuer Angewandte Wissenschaften
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.


import json
import os
import requests
import sys
import signal
from urlparse import urlparse

from keystoneclient.v2_0 import client
from occi.backend import KindBackend
from occi.core_model import Link, Kind, Resource
from occi.exceptions import HTTPError
from occi.registry import NonePersistentRegistry
from occi.wsgi import Application
from tornado import httpserver
from tornado import ioloop
from tornado import wsgi
from wsgiref.simple_server import make_server

from sm.backends import ServiceBackend
from sm.config import CONFIG, CONFIG_PATH
from sm.log import LOG
from sdk.mcn import util
from sdk.mcn.security import KeyStoneAuthService

import jsonpickle
from pymongo import MongoClient
from bson.objectid import ObjectId
from sm.mongo_key_replacer import KeyTransform
from ConfigParser import NoSectionError

__author__ = 'andy'


class SMRegistry(NonePersistentRegistry):

    def __init__(self):
        super(SMRegistry, self).__init__()

    def add_resource(self, key, resource, extras):
        self.resources[resource.identifier] = resource

    def get_resource(self, key, extras):
        result = None
        if key in self.resources:
            if self.resources[key].extras is not None and self.resources[key].extras['tenant_name'] == self.get_extras(extras):
                result = self.resources[key]
        return result

    def get_resources(self, extras):
        result = []
        for item in self.resources.values():
            if item.extras is not None and \
                    item.extras['tenant_name'] == self.get_extras(extras):
                result.append(item)
        return result

    def get_extras(self, extras):
        return extras['tenant_name']

#TODO(somebody): replace mongo implementation with something that actually works
class MongoRegistry(NonePersistentRegistry):
    def __init__(self, mongo_addr):
        if mongo_addr is not None:
            super(MongoRegistry, self).__init__()
            self.mongo_resources = MongoConnection(mongo_addr).resources_coll
            resources = self.mongo_resources.find_one()
            self.o_id = str(ObjectId())
            if resources is not None:
                self.o_id = resources.pop('_id')
                self.resources = jsonpickle.decode(json.dumps(resources))
        else:
            raise AttributeError('No mongo address provided')

    def save_resources_registry(self):
        res = json.loads(jsonpickle.encode(self.resources))
        if self.o_id is not None:
            res['_id'] = self.o_id
        self.mongo_resources.save(res)

    def add_resource(self, key, resource, extras):
        super(MongoRegistry, self).add_resource(key, resource, extras)
        LOG.debug('saving '+key+' to resources on Mongo.')
        self.save_resources_registry()

    def delete_resource(self, key, extras):
        super(MongoRegistry, self).delete_resource(key, extras)
        self.save_resources_registry()


class SMMongoRegistry(MongoRegistry):
    def __init__(self, mongo_addr):
        super(SMMongoRegistry, self).__init__(mongo_addr)

    def add_resource(self, key, resource, extras):
        self.resources[resource.identifier] = resource
        LOG.debug('saving '+resource.identifier+' to resources on Mongo.')
        self.save_resources_registry()

    def get_resource(self, key, extras):
        result = None
        if key in self.resources:
            if self.resources[key].extras is not None and self.resources[key].extras['tenant_name'] == \
                    self.get_extras(extras):
                result = self.resources[key]
        return result

    def get_resources(self, extras):
        result = []
        for item in self.resources.values():
            if item.extras is not None and \
                    item.extras['tenant_name'] == self.get_extras(extras):
                result.append(item)
        return result

    def get_extras(self, extras):
        return extras['tenant_name']


class MongoConnection:
    def __init__(self, db_host):
        connection = MongoClient(db_host)
        resources_db = connection.resources_db
        resources_db.add_son_manipulator(KeyTransform(".", "_dot_"))
        self.resources_coll = resources_db.resource_coll


class MApplication(Application):

    def __init__(self):
        try:
            # added try as many sm.cfg still have no mongo section
            mongo_addr = CONFIG.get('mongo', 'host', None)
        except NoSectionError:
            mongo_addr = None

        sm_name = os.environ.get('SM_NAME', 'SAMPLE_SM')
        mongo_service_name = sm_name.upper().replace('-', '_')

        db_host_key = mongo_service_name + '_MONGO_SERVICE_HOST'
        db_port_key = mongo_service_name + '_MONGO_SERVICE_PORT'
        db_host = os.environ.get(db_host_key, False)
        db_port = os.environ.get(db_port_key, False)
        db_user = os.environ.get('DB_USER', False)
        db_password = os.environ.get('DB_PASSWORD', False)

        if db_host and db_port and db_user and db_password:
            mongo_addr = 'mongodb://%s:%s@%s:%s' % (db_user, db_password, db_host, db_port)

        if mongo_addr is None:
            reg = SMRegistry()
        else:
            reg = SMMongoRegistry(mongo_addr)
        super(MApplication, self).__init__(reg)

        self.register_backend(Link.kind, KindBackend())

    def register_backend(self, category, backend):
        return super(MApplication, self).register_backend(category, backend)

    def __call__(self, environ, response):
        token = environ.get('HTTP_X_AUTH_TOKEN', '')

        if token == '':
            LOG.error('No X-Auth-Token header supplied.')
            raise HTTPError(400, 'No X-Auth-Token header supplied.')

        tenant = environ.get('HTTP_X_TENANT_NAME', '')

        if tenant == '':
            LOG.error('No X-Tenant-Name header supplied.')
            raise HTTPError(400, 'No X-Tenant-Name header supplied.')

        design_uri = CONFIG.get('service_manager', 'design_uri', '')
        if design_uri == '':
                LOG.fatal('No design_uri parameter supplied in sm.cfg')
                raise Exception('No design_uri parameter supplied in sm.cfg')

        auth = KeyStoneAuthService(design_uri)
        if not auth.verify(token=token, tenant_name=tenant):
            raise HTTPError(401, 'Token is not valid. You likely need an updated token.')

        return self._call_occi(environ, response, token=token, tenant_name=tenant, registry=self.registry)


class Service:

    def __init__(self, app, srv_type=None):
        # openstack objects tracking the keystone service and endpoint
        self.srv_ep = None
        self.ep = None
        self.DEBUG = True

        self.app = app
        self.service_backend = ServiceBackend(app)
        LOG.info('Using configuration file: ' + CONFIG_PATH)

        self.token, self.tenant_name = self.get_service_credentials()
        self.design_uri = CONFIG.get('service_manager', 'design_uri', '')
        if self.design_uri == '':
                LOG.fatal('No design_uri parameter supplied in sm.cfg')
                raise Exception('No design_uri parameter supplied in sm.cfg')

        self.stg = None
        stg_path = CONFIG.get('service_manager', 'manifest', '')
        if stg_path == '':
            raise RuntimeError('No STG specified in the configuration file.')
        with open(stg_path) as stg_content:
            self.stg = json.load(stg_content)
            stg_content.close()

        if not srv_type:
            srv_type = self.create_service_type()
        self.srv_type = srv_type

        self.reg_srv = CONFIG.getboolean('service_manager_admin', 'register_service')
        if self.reg_srv:
            self.region = CONFIG.get('service_manager_admin', 'region', '')
            if self.region == '':
                LOG.info('No region parameter specified in sm.cfg, defaulting to an OpenStack default: RegionOne')
                self.region = 'RegionOne'
            self.service_endpoint = CONFIG.get('service_manager_admin', 'service_endpoint')
            if self.service_endpoint != '':
                LOG.warn('DEPRECATED: service_endpoint parameter supplied in sm.cfg! Endpoint is now specified in '
                         'service manifest as service_endpoint')
            LOG.info('Using ' + self.stg['service_endpoint'] + ' as the service_endpoint value '
                                                               'from service manifest')
            up = urlparse(self.stg['service_endpoint'])
            self.service_endpoint = up.scheme + '://' + up.hostname + ':' + str(up.port)

    def get_service_credentials(self):
        token = CONFIG.get('service_manager_admin', 'service_token', '')
        if token == '':
            raise Exception('No service_token parameter supplied in sm.cfg')
        tenant_name = CONFIG.get('service_manager_admin', 'service_tenant_name', '')
        if tenant_name == '':
            raise Exception('No tenant_name parameter supplied in sm.cfg')

        return token, tenant_name

    def register_extension(self, mixin, backend):
        self.app.register_backend(mixin, backend)

    def register_service(self):

        self.srv_ep = util.services.get_service_endpoint(identifier=self.srv_type.term, token=self.token,
                                                         endpoint=self.design_uri, tenant_name=self.tenant_name,
                                                         url_type='public')

        if self.srv_ep is None or self.srv_ep == '':
            LOG.debug('Registering the service with the keystone service...')

            keystone = client.Client(token=self.token, tenant_name=self.tenant_name, auth_url=self.design_uri)

            # taken from the kind definition
            self.srv_ep = keystone.services.create(
                self.srv_type.scheme+self.srv_type.term,
                self.srv_type.scheme+self.srv_type.term,
                self.srv_type.title)

            internal_url = admin_url = public_url = self.service_endpoint

            self.ep = keystone.endpoints.create(self.region, self.srv_ep.id, public_url, admin_url, internal_url)
            LOG.info('Service is now registered with keystone: ' +
                     'Region: ' + self.ep.region +
                     ' Public URL:' + self.ep.publicurl +
                     ' Service ID: ' + self.srv_ep.id +
                     ' Endpoint ID: ' + self.ep.id)
        else:
            LOG.info('Service is already registered with keystone. Service endpoint is: ' + self.srv_ep)

    def shutdown_handler(self, signum=None, frame=None):
        LOG.info('Service shutting down... ')
        if not self.DEBUG:
            ioloop.IOLoop.instance().add_callback(self.deregister_service())
        else:
            self.deregister_service()

    def deregister_service(self):
        if not self.reg_srv:
            return
        if self.srv_ep:
            LOG.debug('De-registering the service with the keystone service...')
            keystone = client.Client(token=self.token, tenant_name=self.tenant_name, auth_url=self.design_uri)
            keystone.services.delete(self.srv_ep.id)  # deletes endpoint too
            if not self.DEBUG:
                ioloop.IOLoop.instance().stop()
            else:
                sys.exit(0)

    def run(self):
        self.app.register_backend(self.srv_type, self.service_backend)

        if self.reg_srv:
            self.register_service()

            # setup shutdown handler for de-registration of service
            for sig in [signal.SIGTERM, signal.SIGINT, signal.SIGHUP, signal.SIGQUIT]:
                signal.signal(sig, self.shutdown_handler)

        up = urlparse(self.stg['service_endpoint'])
        dep_port = CONFIG.get('general', 'port')
        if dep_port != '':
            LOG.warn('DEPRECATED: parameter general: port in service manager config. '
                     'Service port number (' + str(up.port) + ') is taken from the service manifest')

        if self.DEBUG:
            LOG.debug('Using WSGI reference implementation, listening on 0.0.0.0:%s' % str(up.port))
            httpd = make_server('0.0.0.0', int(up.port), self.app)
            httpd.serve_forever()
        else:
            LOG.debug('Using tornado implementation, listening on 0.0.0.0:%s' % str(up.port))
            container = wsgi.WSGIContainer(self.app)
            http_server = httpserver.HTTPServer(container)
            http_server.listen(int(up.port))
            ioloop.IOLoop.instance().start()

        LOG.info('Service Manager running on interfaces, running on port: ' + str(up.port))

    def get_category(self, svc_kind):
        keystone = client.Client(token=self.token, tenant_name=self.tenant_name, auth_url=self.design_uri)

        try:
            svc = keystone.services.find(type=svc_kind.keys()[0])
            svc_ep = keystone.endpoints.find(service_id=svc.id)
        except Exception as e:
            LOG.error('Cannot find the service endpoint of: ' + svc_kind.__repr__())
            raise e

        u = urlparse(svc_ep.publicurl)

        # sort out the OCCI QI path
        if u.path == '/':
            svc_ep.publicurl += '-/'
        elif u.path == '':
            svc_ep.publicurl += '/-/'
        else:
            LOG.warn('Service endpoint URL does not look like it will work: ' + svc_ep.publicurl.__repr__())
            svc_ep.publicurl = u.scheme + '://' + u.netloc + '/-/'
            LOG.warn('Trying with the scheme and net location: ' + svc_ep.publicurl.__repr__())

        heads = {'X-Auth-Token': self.token, 'X-Tenant-Name': self.tenant_name, 'Accept': 'application/occi+json'}

        try:
            r = requests.get(svc_ep.publicurl, headers=heads)
            r.raise_for_status()
        except requests.HTTPError as err:
            LOG.error('HTTP Error: should do something more here!' + err.message)
            raise err

        registry = json.loads(r.content)

        category = None
        for cat in registry:
            if 'related' in cat:
                category = cat

        return Kind(scheme=category['scheme'], term=category['term'], related=category['related'],
                    title=category['title'], attributes=category['attributes'], location=category['location'])

    def get_dependencies(self):
        dependent_kinds = []
        for svc_type in self.stg['depends_on']:
            c = self.get_category(svc_type)
            if c:
                dependent_kinds.append(c)

        dependent_kinds.append(Resource.kind)
        return dependent_kinds

    def create_service_type(self):

        required_occi_kinds = self.get_dependencies()

        svc_scheme = self.stg['service_type'].split('#')[0] + '#'
        svc_term = self.stg['service_type'].split('#')[1]

        return Kind(
            scheme=svc_scheme,
            term=svc_term,
            related=required_occi_kinds,
            title=self.stg['service_description'],
            attributes=self.stg['service_attributes'],
            location='/' + svc_term + '/'
        )
