# Copyright 2014-2015 Zuercher Hochschule fuer Angewandte Wissenschaften
# All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
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


from occi.backend import KindBackend

from sm.config import CONFIG
manager = CONFIG.get('general', 'manager', default='so_manager')

# generic manager stuff
from sm.managers.generic import ServiceParameters
from sm.managers.generic import AsychExe

# depending on config, we import a different manager and ensure consistent names
if manager == 'so_manager':
    from sm.managers.so_manager import InitSO as Init
    from sm.managers.so_manager import ActivateSO as Activate
    from sm.managers.so_manager import DeploySO as Deploy
    from sm.managers.so_manager import ProvisionSO as Provision
    from sm.managers.so_manager import RetrieveSO as Retrieve
    from sm.managers.so_manager import UpdateSO as Update
    from sm.managers.so_manager import DestroySO as Destroy
elif manager == 'openbaton':
    from sm.managers.openbaton_manager import Init
    from sm.managers.openbaton_manager import Activate
    from sm.managers.openbaton_manager import Deploy
    from sm.managers.openbaton_manager import Provision
    from sm.managers.openbaton_manager import Retrieve
    from sm.managers.openbaton_manager import Update
    from sm.managers.openbaton_manager import Destroy

__author__ = 'andy'

# service state model:
#  - initialise
#  - activate
#  - deploy
#  - provision -> This is THE terminal state
#  - "active" (entered into runtime ops) This is not used
#  - update
#  - destroy
#  - fail


class ServiceBackend(KindBackend):
    """
    Provides the basic functionality required to CRUD SOs
    """
    def __init__(self, app):
        self.registry = app.registry
        # these are read from a location specified in sm,cfg, service_manager::service_params
        self.srv_prms = ServiceParameters()

    def create(self, entity, extras):
        super(ServiceBackend, self).create(entity, extras)
        extras['srv_prms'] = self.srv_prms
        # create the SO container
        Init(entity, extras).run()
        # run background tasks
        # TODO this would be better using a workflow engine!
        AsychExe([Activate(entity, extras), Deploy(entity, extras),
                  Provision(entity, extras)], self.registry).start()

    def retrieve(self, entity, extras):
        super(ServiceBackend, self).retrieve(entity, extras)
        Retrieve(entity, extras).run()

    def delete(self, entity, extras):
        super(ServiceBackend, self).delete(entity, extras)
        extras['srv_prms'] = self.srv_prms
        AsychExe([Destroy(entity, extras)]).start()

    def update(self, old, new, extras):
        super(ServiceBackend, self).update(old, new, extras)
        extras['srv_prms'] = self.srv_prms
        Update(old, extras, new).run()

    def replace(self, old, new, extras):
        raise NotImplementedError()
