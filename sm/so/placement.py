"""Linear programming examples that show how to use the APIs."""

from google.apputils import app

from ortools.linear_solver import pywraplp
from sdk import services
from google.apputils import app
from ortools.constraint_solver import pywrapcp
from sm.config import CONFIG, CONFIG_PATH

import json
import requests
import logging

def config_logger(log_level=logging.DEBUG):
    logging.basicConfig(format='%(threadName)s \t %(levelname)s %(asctime)s: \t%(message)s',
                        datefmt='%m/%d/%Y %I:%M:%S %p',
                        log_level=log_level)
    logger = logging.getLogger(__name__)
    logger.setLevel(log_level)
    return logger

LOG = config_logger()

# data
lat_path = CONFIG.get("service_manager", "dc_lats", None)
latencies = None
if lat_path is not None:
    with open(lat_path) as dc_lats_file:
        latencies = json.load(dc_lats_file)
if latencies is None:
    raise RuntimeError("Latencies between DC files not provided - Placement decision impossible.")
# latencies = {"bart.cloudcomplab.ch_bart.cloudcomplab.ch": 1,
#              "cloudsigma.com_cloudsigma.com": 1,
#              "bart.cloudcomplab.ch_cloudsigma.com": 50,
#              "cloudsigma.com_bart.cloudcomplab.ch": 50}

# matrix: source_dest


class Placement(object):
    def __init__(self, token, tenant):
        self.token = token
        self.tenant = tenant

    def place_services(self, svc_type_endpoint, optimize_for):

        for svc_endpoint in svc_type_endpoint:
            service = {}

            svc_name = svc_endpoint.keys()[0]
            namespace = svc_name.split('#')[0]
            svc_term = svc_name.split('#')[1]

            LOG.debug("Placement service placing service type: " + str(svc_term))

            service["name"] = svc_term
            service["service"] = {}
            updated_endpoints = []

            for endpoint in svc_endpoint[svc_name]['endpoint']:
                # vals = self.__get_attributes(namespace, svc_term, svc_endpoint[svc_name]['endpoint'][0]['publicURL'])
                vals = self.__get_attributes(namespace, svc_term, endpoint)
                # endpoint.update(vals)
                endpoint_dict = {"url": endpoint}
                endpoint_dict.update(vals)
                updated_endpoints.append(endpoint_dict)
            svc_endpoint[svc_name]['endpoint'] = updated_endpoints


        # building the structure for solver
        service_structure = []
        for svc_endpoint in svc_type_endpoint:
            service = {}
            svc_name = svc_endpoint.keys()[0]
            svc_term = svc_name.split('#')[1]
            service["name"] = svc_term
            maxLatencies = []
            new_inputs = []
            for inp in svc_endpoint[svc_name]["inputs"]:
                name = inp.split("#")[1]
                if name.split('.')[0] == "latency":
                    new_max_lat_name = name.split('.')[1]
                    new_max_lat_val = svc_endpoint[svc_name]["inputs"][inp]
                    maxLatencies.append({"name": new_max_lat_name, "max": new_max_lat_val})
                else:
                    new_inputs.append(inp)
            svc_endpoint[svc_name]['inputs'] = new_inputs
            service["service"] = {"endpoints": svc_endpoint[svc_name]["endpoint"], "maxLatencies": maxLatencies}
            service_structure.append(service)

        # service stucture ready to be used by solver
        services_to_place = self.__run_placement(service_structure, optimize_for)
        for svc_type in svc_type_endpoint:
            svc_name = svc_type.keys()[0]
            svc_term = svc_name.split('#')[1]
            svc_type[svc_name]['endpoint'] = services_to_place[svc_term]
            LOG.debug("Placing service " + svc_term + " on endpoint: " + services_to_place[svc_term])

        LOG.debug("Services placement completed.")
        # for service in

        return svc_type_endpoint

    def __get_attributes(self, namespace, svc_term, svc_endpoint):
        headers = {'X-Auth-Token': self.token,
                   'X-Tenant-Name': self.tenant,
                   'Content-Type': 'text/occi',
                   'Accept': 'application/occi+json'}

        LOG.debug("Requesting registered Categories for service: " + svc_term + " at endpoint: " + svc_endpoint)

        url = svc_endpoint + '/-/'
        r = requests.get(url, headers=headers)
        rjson = json.loads(r.text)
        svc_attrs = None
        svc_cost = None
        svc_dc = None
        for svc_category in rjson:
            # if svc_category.get('term') == svc_term:
            #     svc_attrs = svc_category.get('attributes')
            #     # todo: this is not very occi compliant
            #     svc_dc = svc_category.get('related')[0]
            if svc_category.get(
                    'scheme') == 'http://schemas.mobile-cloud-networking.eu/occi/sm/cost#':
                    # todo: put this scheme definition somewhere in the config to avoid hardcoding
                svc_cost = svc_category.get('term')
            if svc_category.get(
                    'scheme') == 'http://schemas.mobile-cloud-networking.eu/occi/sm/location#':
                    # todo: put this scheme definition somewhere in the config to avoid hardcoding
                svc_dc = svc_category.get('term')
        if not svc_dc or not svc_cost:
            raise RuntimeError("Could not find service attributes for service: " + svc_term)

        LOG.debug("Service " + svc_term + " endpoint at location: " + svc_endpoint +
                  " has cost: " + svc_cost + " and dc: " + svc_dc)

        svc_placement_data = {'dc': svc_dc, 'cost': svc_cost}


        return svc_placement_data

    @staticmethod
    def __run_placement(services_to_place, optimize_for):
        solver = pywrapcp.Solver('RunPlacement')
        x = []
        prices = []
        svc_vars = {}
        svc_types = {}
        for svc_type in services_to_place:
            svc_types[svc_type['name']] = []

            for svc_endpoint in svc_type["service"]['endpoints']:
                var = solver.IntVar(0, 1, str("x_##_%s_##_%s" % (svc_type["name"], svc_endpoint['dc'])))
                x.append(var)
                prices.append(svc_endpoint['cost'])
                svc_vars[svc_type['name'] + "_" + svc_endpoint["dc"]] = {"var": var, "dc": svc_endpoint["dc"],
                                                                         "svc_type": svc_type["name"]}
                svc_types[svc_type['name']].append(var)
            # place service of type svc_type['name'] only once
            solver.Add(solver.Sum(svc_types[svc_type['name']]) == 1)

        for src_svc in services_to_place:
            for svc_lat in src_svc["service"]['maxLatencies']:
                dest_svc_name = svc_lat["name"]
                # lookup the service in a list by name
                dest_svc = [svc for svc in services_to_place if svc["name"] == dest_svc_name][0]
                # for each possible placement of src_svc
                for src_plc in src_svc["service"]["endpoints"]:
                    src_dc = src_plc["dc"]
                    # for each possible placement of the destination svc
                    for dest_plc in dest_svc["service"]["endpoints"]:
                        # multiply the placement variables with the latency across DCs
                        dest_dc = dest_plc["dc"]
                        solver.Add(((svc_vars[src_svc["name"] + "_" + src_dc]["var"] *
                                     svc_vars[dest_svc["name"] + "_" + dest_dc]["var"]) * latencies[
                                        src_dc + "_" + dest_dc]) <= svc_lat["max"] * 2)


        # objective
        cost = solver.Sum([x[i] * int(prices[i]) for i in range(0, len(x))])
        objective = solver.Minimize(cost, 1)

        # solution
        db = solver.Phase(x,
                  solver.CHOOSE_MIN_SIZE_LOWEST_MAX,
                  solver.ASSIGN_CENTER_VALUE)

        solver.NewSearch(db)

        # Iterates through the solutions, displaying each.
        solution = None
        current_cost = None
        while solver.NextSolution():
            placed = [y for y in x if y.Value() == 1]
            cost = sum([x[i].Value() * int(prices[i]) for i in range(0, len(prices))])
            # optimize for cost yes / no as a variable
            if optimize_for == "min_cost":
                if current_cost is None or current_cost > cost:
                    current_cost = cost
                    solution = placed
            elif optimize_for == "max_cost":
                if current_cost is None or current_cost < cost:
                    current_cost = cost
                    solution = placed

        solver.EndSearch()

        if solution is None:
            raise RuntimeError("No placement solution found.")

        services_locations = {}
        for sv in solution:
            name = sv.Name()
            var = name.split('_##_')
            services_locations[var[1]] = var[2]

        services_by_name = {}
        for service in services_to_place:
            # this supposes there are no more than one time the same type of service within the same dc
            service['service']['endpoints'] = [x for x in service['service']['endpoints'] if str(x['dc']) == services_locations[service['name']]]
            services_by_name[service['name']] = service['service']['endpoints'][0]['url']

        return services_by_name
