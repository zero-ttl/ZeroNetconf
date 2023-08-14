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

class NeighsAdvRoutes:
    def __init__(self, user, pwd, router_name, peerips, table = None):
        self.router_name = router_name
        self.peer_ips = peerips
        if table:
            self.instance = table
        else:
            self.instance = "inet.0"

        dev = Device(host=self.router_name, user=user, passwd=pwd)
        dev.open()


        self.routeTable = {}
        self.neighgroup = {}

        if self.peer_ips != None:
            for peer_ip in self.peer_ips:
                self.routeTable[peer_ip] = {}
                for k,v in AdvertisedRouteTable(dev).get(table=self.instance,neighbor=peer_ip).items():
                    self.routeTable[peer_ip][k[0]] = dict(v)

                test = NeighGroupTable(dev).get(neighbor_address=peer_ip)
                for k,v in test.items():
                    self.neighgroup[peer_ip] = dict(v)['group']

        #print(self.neighgroup)

        dev.close()

        #calculate aspath_len
        for peerip in self.routeTable.keys():
            for prefix in self.routeTable[peerip].keys():
                self.routeTable[peerip][prefix]['aspath_len'] = max(1,len(self.routeTable[peerip][prefix]['aspath'].split())-1)
                self.routeTable[peerip][prefix]['peer_group'] = self.neighgroup[peerip]



    def getRoutes(self):
         # Output example --> routeTable[peerip][prefix][aspath|aspath_len|med]
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
    print("ZeroNetconf is running... ")
    try:
        config = config_parser('./config/exporter.conf')
    except ZeroConfException as e:
        print(e)
        sys.exit(0)

    print("ZeroNetconf configuration retrieved ")
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
                               ['device', 'peer', 'peer_group', 'prefix', 'prefix_group'])
            med = Gauge('zm_bgpadvroutes_med', 'BGP Advertised Routes by ZeroTTL - med',
                        ['device', 'peer', 'peer_group', 'prefix', 'prefix_group'])

            while True: #execute and wait...
                print(" %s is starting" % batch)
                capturedpref = {}
                for host_subsection in batch_descr.values():
                    router = host_subsection['router']
                    peers = host_subsection['peers']
                    instance = host_subsection['instance']
                    print(" connecting to %s" % router)
                    try:
                        capturedpref[router] = NeighsAdvRoutes(user, pwd, router, peers, instance).getRoutes()
                    except ZeroConfException as e:
                        print(e)
                        continue
                    except ConnectError as err:
                        print("Cannot connect to device: {0}".format(err))
                        continue
                    # except Exception as err:
                    #     print(err)
                print(" %s complete" % batch)

                #grouping prefixes
                group_set = set()
                group_table = {}
                group_dict = {}
                pref_group = {}
                for host, peerhostpref in capturedpref.items():
                    for peer, hostpref in peerhostpref.items():
                        for pref, bgp_attr in hostpref.items():
                            if pref not in group_table.keys():
                                group_table[pref]= {}
                            group_table[pref][host+"_"+peer] = bgp_attr

                for pref, host_peer_attr in group_table.items():
                   aux_list = []
                   for hostpeer, bgp_attr in host_peer_attr.items():
                       aux_list.append( (hostpeer,bgp_attr['aspath_len'],bgp_attr['med']) )
                   group_set.add(tuple(aux_list))
                id = 1 #enumerate group
                for group in group_set:
                    group_dict["group"+str(id)] = group
                    id=id+1

                for pref, host_peer_attr in group_table.items():
                    aux_list = []
                    for hostpeer, bgp_attr in host_peer_attr.items():
                        aux_list.append((hostpeer, bgp_attr['aspath_len'], bgp_attr['med']))
                    for k,v in group_dict.items():
                        if tuple(aux_list) == v:
                            pref_group[pref] = k

                #update metrics
                for host, peerhostpref in capturedpref.items():
                    for peer, hostpref in peerhostpref.items():
                        for pref, attr in hostpref.items():
                            aspath_len.labels(host, peer, host+"_"+attr['peer_group'], pref, pref_group[pref]).set(attr['aspath_len'])
                            med.labels(host, peer, host+"_"+attr['peer_group'], pref, pref_group[pref]).set(attr['med'])

                time.sleep(sleeping_period)





