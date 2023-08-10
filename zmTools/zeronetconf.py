import sys
import ast
import configparser
from prometheus_client import start_http_server, Gauge
import time
from jnpr.junos import Device
from jnpr.junos.exception import *
""" Pythonifier for Table/View """
from jnpr.junos.factory import loadyaml
from os.path import splitext
_YAML_ = splitext(__file__)[0] + '.yml'
globals().update(loadyaml(_YAML_))

def config_parser(filepath):
    config = configparser.ConfigParser()
    config = configparser.ConfigParser(empty_lines_in_values=False)
    config.read(filepath)

    sections = {}
    if 'default' not in config.sections():
        raise ZeroConfException('default section is missing')

    for sec in config.sections():
        if sec == 'default':
            default_mandatory_keys = ['username', 'password']
            default_keys = list(config['default'].keys())
            for k in default_mandatory_keys:
                if k not in default_keys:
                    raise ZeroConfException('%s is missing in default section' % k)

            sections['default'] = {}
            for k in default_keys:
                sections['default'][k] = config['default'][k]
        elif sec.find(".") == -1 : #batch
            batch_mandatory_keys = ['action', 'server_port', 'sleeping_period']
            batch_keys = list(config[sec].keys())
            for k in batch_mandatory_keys:
                if k not in batch_keys:
                    raise ZeroConfException('%s is missing in %s section' % (k,sec))

            sections[sec] = {}
            for k in batch_keys:
                val = None
                if k == 'port':
                    val = int(config[sec][k])
                else:
                    val = config[sec][k].strip("'")
                sections[sec][k] = val
        else:
            [batch,subsec] = sec.split(".")
            sections[batch][subsec] = {}
            subsection_keys = list(config[sec].keys())
            subsection_mandatory_keys = []
            if sections[batch]['action'] == 'getBgpAdvPrefixes':
                subsection_mandatory_keys = ['router', 'peers', 'instance']
            for k in subsection_mandatory_keys:
                if k not in subsection_keys:
                    raise ZeroConfException('%s is missing in %s section' % (k, sec))

            for k in subsection_keys:
                val = None
                if k == 'peers':
                    val = [p.strip() for p in ast.literal_eval(config[sec][k])]
                else:
                    val = config[sec][k].strip("'")
                sections[batch][subsec][k] = val


    return sections

class NeighAdvRoutes:
    #
    # a Neigh object should store data related to a neighbor
    #   it can raise ZeroConfException
    #
    def __init__(self, device, routername, peerips, table = None):
        self.routername = routername
        self.peerips = peerips
        if table:
            self.instance = table
        else:
            self.instance = "inet.0"

        if self.peerips != None:
            self.routeTable = {}
            for peerip in self.peerips:
                self.routeTable[peerip] = {}
                for k,v in AdvertisedRouteTable(device).get(table=self.instance,neighbor=peerip).items():
                    self.routeTable[peerip][k[0]] = dict(v)
        else:
            raise ZeroConfException('RouteList - Bad Neighbor IP: "%s" ' % self.peerip)

        for peerip in self.routeTable.keys():
            for prefix in self.routeTable[peerip].keys():
                self.routeTable[peerip][prefix]['aspath_len'] = max(1,len(self.routeTable[peerip][prefix]['aspath'].split())-1)

    def getRoutes(self):
         # Output example
         # {'10.11.12.0/24': {'aspath': '65000 65000 [65000] I',
         #              'aspath_len': 3,
         #              'med': '150'},
         #    '128.0.0.0/23': {'aspath': '65000 65000 [65000] I',
         #            'aspath_len': 3,
         #            'med': '150'},
         return self.routeTable

class ZeroConfException(Exception):
    pass


if __name__ == '__main__':
    print("netconf2prom is running")
    config = config_parser('/config/exporter.conf')
    user = config['default']['username']
    pwd= config['default']['password']

    del config['default']
    if len(config) > 1 :
        raise ZeroConfException("more than one batch is configured")
    # Now: 1 batch, 1 action, single port
    # Tomorrow: N batches, M actions, Z ports (multiprocess option in prom_client if O< N)

    for batch, batch_descr in config.items():

        sleeping_period = int(batch_descr.pop('sleeping_period'))
        server_port = int(batch_descr.pop('server_port'))
        # Start up the server to expose the metrics.
        start_http_server(server_port)

        action = batch_descr.pop('action')
        if action == 'getBgpAdvPrefixes':
            # Create metrics
            aspath_len = Gauge('zm_bgpadvroutes_aslen', 'BGP Advertised Routes by ZeroTTL - AS path size',
                               ['device', 'peer', 'prefix'])
            med = Gauge('zm_bgpadvroutes_med', 'BGP Advertised Routes by ZeroTTL - med',
                        ['device', 'peer', 'prefix'])

            while True: #execute and wait...
                print(" %s is starting" % batch)
                capturedpref = {}
                for host_subsection in batch_descr.values():
                    router = host_subsection['router']
                    peers = host_subsection['peers']
                    instance = host_subsection['instance']
                    dev = Device(host=router, user=user, passwd=pwd)
                    try:
                        dev.open()
                    except ConnectError as err:
                        print("Cannot connect to device: {0}".format(err))
                        sys.exit(1)
                    except Exception as err:
                        print(err)

                    capturedpref[router] = NeighAdvRoutes(dev, router, peers, instance).getRoutes()
                    print(" %s complete" % batch)
                    dev.close()

                #update metrics
                for host, peerhostpref in capturedpref.items():
                    for peer, hostpref in peerhostpref.items():
                        for pref, bgp_attr in hostpref.items():
                            aspath_len.labels(host, peer, pref).set(bgp_attr['aspath_len'])
                            med.labels(host, peer, pref).set(bgp_attr['med'])

                time.sleep(sleeping_period)
    #print (config)




