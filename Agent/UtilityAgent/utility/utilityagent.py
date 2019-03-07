from __future__ import absolute_import
from datetime import datetime, timedelta
import logging
import sys
import json
import random
import copy
import time
import atexit
import math
import operator

from volttron.platform.vip.agent import Agent, BasicCore, core, Core, PubSub, compat
from volttron.platform.agent import utils
from volttron.platform.messaging import headers as headers_mod

#from ACMGClasses.CIP import wrapper
from ACMGAgent.CIP import tagClient
from ACMGAgent.Resources.misc import listparse, schedule
from ACMGAgent.Resources.mathtools import graph
from ACMGAgent.Resources import resource, groups, control, customer
from ACMGAgent.Agent import HomeAgent

from . import settings
from zmq.backend.cython.constants import RATE
from __builtin__ import True
from bacpypes.vlan import Node
from twisted.application.service import Service
#from _pydev_imps._pydev_xmlrpclib import loads
utils.setup_logging()
_log = logging.getLogger(__name__)

''''the UtilityAgent class represents the owner of the distribution 
infrastructure and chief planner for grid operations'''
class UtilityAgent(Agent):
    resourcePool = []
    standardCustomerEnrollment = {"message_subject" : "customer_enrollment",
                                  "message_type" : "new_customer_query",
                                  "message_target" : "broadcast",
                                  "rereg": False,
                                  "info" : ["name","location","resources","customerType"]
                                  }
    
    standardDREnrollment = {"message_subject" : "DR_enrollment",
                            "message_target" : "broadcast",
                            "message_type" : "enrollment_query",
                            "info" : "name"
                            }
    
       

    uid = 0


    def __init__(self,config_path,**kwargs):
        super(UtilityAgent,self).__init__(**kwargs)
        self.config = utils.load_config(config_path)
        self._agent_id = self.config['agentid']
        self.state = "init"
        
        self.t0 = time.time()
        self.name = self.config["name"]
        self.resources = self.config["resources"]
        self.Resources = []
        self.groupList = []
        self.supplyBidList = []
        self.demandBidList = []
        self.reserveBidList = []
        
        self.outstandingSupplyBids = []
        self.outstandingDemandBids = []
        
        sys.path.append('/usr/lib/python2.7/dist-packages')
        sys.path.append('/usr/local/lib/python2.7/dist-packages')
        print(sys.path)
        import mysql.connector
                      
        #DATABASE STUFF
        self.dbconn = mysql.connector.connect(user='smartgrid',password='ugrid123',host='localhost',database='testdbase')

        cursor = self.dbconn.cursor()
                      
        #recreate database tables
        cursor.execute('DROP TABLE IF EXISTS infmeas')
        cursor.execute('DROP TABLE IF EXISTS faults')
        cursor.execute('DROP TABLE IF EXISTS customers')
        cursor.execute('DROP TABLE IF EXISTS bids')
        cursor.execute('DROP TABLE IF EXISTS prices')
        cursor.execute('DROP TABLE IF EXISTS drevents')
        cursor.execute('DROP TABLE IF EXISTS transactions')
        cursor.execute('DROP TABLE IF EXISTS resources')
        cursor.execute('DROP TABLE IF EXISTS appliances')
        cursor.execute('DROP TABLE IF EXISTS appstate')
        cursor.execute('DROP TABLE IF EXISTS resstate')
        cursor.execute('DROP TABLE IF EXISTS plans')
        cursor.execute('DROP TABLE IF EXISTS efficiency')
        cursor.execute('DROP TABLE IF EXISTS relayfaults')
        cursor.execute('DROP TABLE IF EXISTS topology')
        cursor.execute('DROP TABLE IF EXISTS consumption')
        
        cursor.execute('CREATE TABLE IF NOT EXISTS infmeas (logtime TIMESTAMP, et DOUBLE, period INT, signame TEXT, value DOUBLE)')
        cursor.execute('CREATE TABLE IF NOT EXISTS faults (logtime TIMESTAMP, et DOUBLE, duration DOUBLE, node TEXT)')
        cursor.execute('CREATE TABLE IF NOT EXISTS customers (logtime TIMESTAMP, et DOUBLE, customer_name TEXT, customer_location TEXT)')
        cursor.execute('CREATE TABLE IF NOT EXISTS bids (logtime TIMESTAMP, et DOUBLE, period INT, id BIGINT UNSIGNED, side TEXT, service TEXT, aux_service TEXT, resource_name TEXT, counterparty_name TEXT, accepted BOOLEAN, acc_for TEXT, orig_rate DOUBLE, settle_rate DOUBLE, orig_amount DOUBLE, settle_amount DOUBLE)') 
        cursor.execute('CREATE TABLE IF NOT EXISTS prices (logtime TIMESTAMP, et DOUBLE, period INT, node TEXT, rate REAL)')
        cursor.execute('CREATE TABLE IF NOT EXISTS drevents (logtime TIMESTAMP, et DOUBLE, period INT, type TEXT)')
        cursor.execute('CREATE TABLE IF NOT EXISTS transactions (logtime TIMESTAMP, et DOUBLE, period INT, account_holder TEXT, transaction_type TEXT, amount DOUBLE, balance DOUBLE)')
        cursor.execute('CREATE TABLE IF NOT EXISTS resources (logtime TIMESTAMP, et DOUBLE, name TEXT, type TEXT, owner TEXT, location TEXT, max_power DOUBLE)')
        cursor.execute('CREATE TABLE IF NOT EXISTS appliances (logtime TIMESTAMP, et DOUBLE, name TEXT, type TEXT, owner TEXT, max_power DOUBLE)')
        cursor.execute('CREATE TABLE IF NOT EXISTS appstate (logtime TIMESTAMP, et DOUBLE, period INT, name TEXT, state DOUBLE, power DOUBLE)')
        cursor.execute('CREATE TABLE IF NOT EXISTS resstate (logtime TIMESTAMP, et DOUBLE, period INT, name TEXT, state DOUBLE, connected BOOLEAN, reference_voltage DOUBLE, setpoint DOUBLE, inputV DOUBLE, inputI DOUBLE, outputV DOUBLE, outputI DOUBLE)')
        cursor.execute('CREATE TABLE IF NOT EXISTS plans (logtime TIMESTAMP, et DOUBLE, period INT, planning_time DOUBLE, planner TEXT, cost DOUBLE, action TEXT)')
        cursor.execute('CREATE TABLE IF NOT EXISTS efficiency (logtime TIMESTAMP, et DOUBLE, period INT, generation DOUBLE, consumption DOUBLE, loss DOUBLE, unaccounted DOUBLE)')
        cursor.execute('CREATE TABLE IF NOT EXISTS relayfaults (logtime TIMESTAMP, et DOUBLE, period INT, location TEXT, measured TEXT, resistance DOUBLE)')
        cursor.execute('CREATE TABLE IF NOT EXISTS topology (logtime TIMESTAMP, et DOUBLE, period INT, topology TEXT)')
        cursor.execute('CREATE TABLE IF NOT EXISTS consumption (logtime TIMESTAMP, et DOUBLE, period INT, name TEXT, power DOUBLE)')
        
        cursor.close()              
                      
        #register exit function
        atexit.register(self.exit_handler,self.dbconn)
        
        #build grid model objects from the agent's a priori knowledge of system
        #infrastructure relays
        self.relays =[ groups.Relay("COM_MAIN_USER", "load"),
                       groups.Relay("COM_BUS1_USER", "load"),
                       groups.Relay("COM_BUS1LOAD1_USER", "load"),
                       groups.Relay("COM_BUS1LOAD2_USER", "load"),
                       groups.Relay("COM_BUS1LOAD3_USER", "load"),
                       groups.Relay("COM_BUS1LOAD4_USER", "load"),
                       groups.Relay("COM_BUS1LOAD5_USER", "load"),
                       groups.Relay("COM_BUS2_USER", "load"),
                       groups.Relay("COM_BUS2LOAD1_USER", "load"),
                       groups.Relay("COM_BUS1LOAD2_USER", "load"),
                       groups.Relay("COM_BUS1LOAD3_USER", "load"),
                       groups.Relay("COM_BUS1LOAD4_USER", "load"),
                       groups.Relay("COM_BUS1LOAD5_USER", "load"),
                       groups.Relay("IND_MAIN_USER", "load"),
                       groups.Relay("IND_BUS1_USER", "load"),
                       groups.Relay("IND_BUS1LOAD1_USER", "load"),
                       groups.Relay("IND_BUS1LOAD2_USER", "load"),
                       groups.Relay("IND_BUS1LOAD3_USER", "load"),
                       groups.Relay("IND_BUS1LOAD4_USER", "load"),
                       groups.Relay("IND_BUS1LOAD5_USER", "load"),
                       groups.Relay("IND_BUS2_USER", "load"),
                       groups.Relay("IND_BUS2LOAD1_USER", "load"),
                       groups.Relay("IND_BUS1LOAD2_USER", "load"),
                       groups.Relay("IND_BUS1LOAD3_USER", "load"),
                       groups.Relay("IND_BUS1LOAD4_USER", "load"),
                       groups.Relay("IND_BUS1LOAD5_USER", "load"),
                       groups.Relay("RES_MAIN_USER", "load"),
                       groups.Relay("RES_BUS1_USER", "load"),
                       groups.Relay("RES_BUS1LOAD1_USER", "load"),
                       groups.Relay("RES_BUS1LOAD2_USER", "load"),
                       groups.Relay("RES_BUS1LOAD3_USER", "load"),
                       groups.Relay("RES_BUS1LOAD4_USER", "load"),
                       groups.Relay("RES_BUS1LOAD5_USER", "load"),
                       groups.Relay("RES_BUS2_USER", "load"),
                       groups.Relay("RES_BUS2LOAD1_USER", "load"),
                       groups.Relay("RES_BUS2LOAD2_USER", "load"),
                       groups.Relay("RES_BUS2LOAD3_USER", "load"),
                       groups.Relay("RES_BUS2LOAD4_USER", "load"),
                       groups.Relay("RES_BUS2LOAD5_USER", "load"),
                       groups.Relay("RES_BUS3_USER", "load"),
                       groups.Relay("RES_BUS3LOAD1_USER", "load"),
                       groups.Relay("RES_BUS3LOAD2_USER", "load"),
                       groups.Relay("RES_BUS3LOAD3_USER", "load"),
                       groups.Relay("RES_BUS3LOAD4_USER", "load"),
                       groups.Relay("RES_BUS3LOAD5_USER", "load"),
                       groups.Relay("RES_BUS4_USER", "load"),
                       groups.Relay("RES_BUS4LOAD1_USER", "load"),
                       groups.Relay("RES_BUS4LOAD2_USER", "load"),
                       groups.Relay("RES_BUS4LOAD3_USER", "load"),
                       groups.Relay("RES_BUS4LOAD4_USER", "load"),
                       groups.Relay("RES_BUS4LOAD5_USER", "load"),
                       
                       ]
                                              
        self.nodes = [ groups.Node("AC.COM.MAIN.MAIN"),
                       groups.Node("AC.COM.BUS1.MAIN"),
                       groups.Node("AC.COM.BUS1.LOAD1"),
                       groups.Node("AC.COM.BUS1.LOAD2"),
                       groups.Node("AC.COM.BUS1.LOAD3"),
                       groups.Node("AC.COM.BUS1.LOAD4"),
                       groups.Node("AC.COM.BUS1.LOAD5"),
                       groups.Node("AC.COM.BUS2.MAIN"),
                       groups.Node("AC.COM.BUS2.LOAD1"),
                       groups.Node("AC.COM.BUS2.LOAD2"),
                       groups.Node("AC.COM.BUS2.LOAD3"),
                       groups.Node("AC.COM.BUS2.LOAD4"),
                       groups.Node("AC.COM.BUS2.LOAD5"),
                       groups.Node("AC.IND.MAIN.MAIN"),
                       groups.Node("AC.IND.BUS1.MAIN"),
                       groups.Node("AC.IND.BUS1.LOAD1"),
                       groups.Node("AC.IND.BUS1.LOAD2"),
                       groups.Node("AC.IND.BUS1.LOAD3"),
                       groups.Node("AC.IND.BUS1.LOAD4"),
                       groups.Node("AC.IND.BUS1.LOAD5"),
                       groups.Node("AC.IND.BUS2.MAIN"),
                       groups.Node("AC.IND.BUS2.LOAD1"),
                       groups.Node("AC.IND.BUS2.LOAD2"),
                       groups.Node("AC.IND.BUS2.LOAD3"),
                       groups.Node("AC.IND.BUS2.LOAD4"),
                       groups.Node("AC.IND.BUS2.LOAD5"),
                       groups.Node("AC.RES.MAIN.MAIN"),
                       groups.Node("AC.RES.BUS1.MAIN"),
                       groups.Node("AC.RES.BUS1.LOAD1"),
                       groups.Node("AC.RES.BUS1.LOAD2"),
                       groups.Node("AC.RES.BUS1.LOAD3"),
                       groups.Node("AC.RES.BUS1.LOAD4"),
                       groups.Node("AC.RES.BUS1.LOAD5"),
                       groups.Node("AC.RES.BUS2.MAIN"),
                       groups.Node("AC.RES.BUS2.LOAD1"),
                       groups.Node("AC.RES.BUS2.LOAD2"),
                       groups.Node("AC.RES.BUS2.LOAD3"),
                       groups.Node("AC.RES.BUS2.LOAD4"),
                       groups.Node("AC.RES.BUS2.LOAD5"),
                       groups.Node("AC.RES.BUS3.MAIN"),
                       groups.Node("AC.RES.BUS3.LOAD1"),
                       groups.Node("AC.RES.BUS3.LOAD2"),
                       groups.Node("AC.RES.BUS3.LOAD3"),
                       groups.Node("AC.RES.BUS3.LOAD4"),
                       groups.Node("AC.RES.BUS3.LOAD5"),
                       groups.Node("AC.RES.BUS4.MAIN"),
                       groups.Node("AC.RES.BUS4.LOAD1"),
                       groups.Node("AC.RES.BUS4.LOAD2"),
                       groups.Node("AC.RES.BUS4.LOAD3"),
                       groups.Node("AC.RES.BUS4.LOAD4"),
                       groups.Node("AC.RES.BUS4.LOAD5"),
                       ]   
#        for node in self.nodes:
#            print "node name: {nodename}".format(nodename = node.name)
#            print node
            
                      
        self.zones=[groups.Zone("AC.COM.MAINgroups.Zone",[self.nodes[0]]),
                    groups.Zone("AC.COM.BUS1.groups.Zone",[self.nodes[1]]),
                    groups.Zone("AC.COM.BUS1.groups.Zone1",[self.nodes[2]]),
                    groups.Zone("AC.COM.BUS1.groups.Zone2",[self.nodes[3]]),
                    groups.Zone("AC.COM.BUS1.groups.Zone3",[self.nodes[4]]),
                    groups.Zone("AC.COM.BUS1.groups.Zone4",[self.nodes[5]]),
                    groups.Zone("AC.COM.BUS1.groups.Zone5",[self.nodes[6]]),
                    groups.Zone("AC.COM.BUS2.groups.Zone",[self.nodes[7]]),
                    groups.Zone("AC.COM.BUS2.groups.Zone1",[self.nodes[8]]),
                    groups.Zone("AC.COM.BUS2.groups.Zone2",[self.nodes[9]]),
                    groups.Zone("AC.COM.BUS2.groups.Zone3",[self.nodes[10]]),
                    groups.Zone("AC.COM.BUS2.groups.Zone4",[self.nodes[11]]),
                    groups.Zone("AC.COM.BUS2.groups.Zone5",[self.nodes[12]]),
                    groups.Zone("AC.IND.MAINgroups.Zone",[self.nodes[13]]),
                    groups.Zone("AC.IND.BUS1.groups.Zone",[self.nodes[14]]),
                    groups.Zone("AC.IND.BUS1.groups.Zone1",[self.nodes[15]]),
                    groups.Zone("AC.IND.BUS1.groups.Zone2",[self.nodes[16]]),
                    groups.Zone("AC.IND.BUS1.groups.Zone3",[self.nodes[17]]),
                    groups.Zone("AC.IND.BUS1.groups.Zone4",[self.nodes[18]]),
                    groups.Zone("AC.IND.BUS1.groups.Zone5",[self.nodes[19]]),
                    groups.Zone("AC.IND.BUS2.groups.Zone",[self.nodes[20]]),
                    groups.Zone("AC.IND.BUS2.groups.Zone1",[self.nodes[21]]),
                    groups.Zone("AC.IND.BUS2.groups.Zone2",[self.nodes[22]]),
                    groups.Zone("AC.IND.BUS2.groups.Zone3",[self.nodes[23]]),
                    groups.Zone("AC.IND.BUS2.groups.Zone4",[self.nodes[24]]),
                    groups.Zone("AC.IND.BUS2.groups.Zone5",[self.nodes[25]]),
                    groups.Zone("AC.RES.MAINgroups.Zone",[self.nodes[26]]),
                    groups.Zone("AC.RES.BUS1.groups.Zone",[self.nodes[27]]),
                    groups.Zone("AC.RES.BUS1.groups.Zone1",[self.nodes[28]]),
                    groups.Zone("AC.RES.BUS1.groups.Zone2",[self.nodes[29]]),
                    groups.Zone("AC.RES.BUS1.groups.Zone3",[self.nodes[30]]),
                    groups.Zone("AC.RES.BUS1.groups.Zone4",[self.nodes[31]]),
                    groups.Zone("AC.RES.BUS1.groups.Zone5",[self.nodes[32]]),
                    groups.Zone("AC.RES.BUS2.groups.Zone",[self.nodes[33]]),
                    groups.Zone("AC.RES.BUS2.groups.Zone1",[self.nodes[34]]),
                    groups.Zone("AC.RES.BUS2.groups.Zone2",[self.nodes[35]]),
                    groups.Zone("AC.RES.BUS2.groups.Zone3",[self.nodes[36]]),
                    groups.Zone("AC.RES.BUS2.groups.Zone4",[self.nodes[37]]),
                    groups.Zone("AC.RES.BUS2.groups.Zone5",[self.nodes[38]]),
                    groups.Zone("AC.RES.BUS3.groups.Zone",[self.nodes[39]]),
                    groups.Zone("AC.RES.BUS3.groups.Zone1",[self.nodes[40]]),
                    groups.Zone("AC.RES.BUS3.groups.Zone2",[self.nodes[41]]),
                    groups.Zone("AC.RES.BUS3.groups.Zone3",[self.nodes[42]]),
                    groups.Zone("AC.RES.BUS3.groups.Zone4",[self.nodes[43]]),
                    groups.Zone("AC.RES.BUS3.groups.Zone5",[self.nodes[44]]),
                    groups.Zone("AC.RES.BUS4.groups.Zone",[self.nodes[45]]),
                    groups.Zone("AC.RES.BUS4.groups.Zone1",[self.nodes[46]]),
                    groups.Zone("AC.RES.BUS4.groups.Zone2",[self.nodes[47]]),
                    groups.Zone("AC.RES.BUS4.groups.Zone3",[self.nodes[48]]),
                    groups.Zone("AC.RES.BUS4.groups.Zone4",[self.nodes[49]]),
                    groups.Zone("AC.RES.BUS4.groups.Zone5",[self.nodes[50]]),
                    
                    ]
        self.Edges = []
        
        #global index for checking relay consistency
        self.edgeindex = 0
        self.Edges.append(self.nodes[0].addEdge(self.nodes[1], "to", "COM_BUS1_CURRENT", [self.relays[1]]))
        self.Edges.append(self.nodes[0].addEdge(self.nodes[7], "to", "COM_BUS2_CURRENT", [self.relays[7]]))
        self.Edges.append(self.nodes[1].addEdge(self.nodes[2], "to", "COM_B1L1_CURRENT", [self.relays[2]]))              
        self.Edges.append(self.nodes[1].addEdge(self.nodes[3], "to", "COM_B1L2_CURRENT", [self.relays[3]]))
        self.Edges.append(self.nodes[1].addEdge(self.nodes[4], "to", "COM_B1L3_CURRENT", [self.relays[4]]))              
        self.Edges.append(self.nodes[1].addEdge(self.nodes[5], "to", "COM_B1L4_CURRENT", [self.relays[5]]))              
        self.Edges.append(self.nodes[1].addEdge(self.nodes[6], "to", "COM_B1L5_CURRENT", [self.relays[6]]))
        self.Edges.append(self.nodes[7].addEdge(self.nodes[8], "to", "COM_B2L1_CURRENT", [self.relays[8]]))                                                        
        self.Edges.append(self.nodes[7].addEdge(self.nodes[9], "to", "COM_B2L2_CURRENT", [self.relays[9]]))
        self.Edges.append(self.nodes[7].addEdge(self.nodes[10], "to", "COM_B2L3_CURRENT", [self.relays[10]]))
        self.Edges.append(self.nodes[7].addEdge(self.nodes[11], "to", "COM_B2L4_CURRENT", [self.relays[11]]))
        self.Edges.append(self.nodes[7].addEdge(self.nodes[12], "to", "COM_B2L5_CURRENT", [self.relays[12]]))
        self.Edges.append(self.nodes[13].addEdge(self.nodes[14], "to", "IND_BUS1_CURRENT", [self.relays[14]]))
        self.Edges.append(self.nodes[13].addEdge(self.nodes[20], "to", "IND_BUS2_CURRENT", [self.relays[20]]))
        self.Edges.append(self.nodes[14].addEdge(self.nodes[15], "to", "IND_B1L1_CURRENT", [self.relays[15]]))
        self.Edges.append(self.nodes[14].addEdge(self.nodes[16], "to", "IND_B1L2_CURRENT", [self.relays[16]]))
        self.Edges.append(self.nodes[14].addEdge(self.nodes[17], "to", "IND_B1L3_CURRENT", [self.relays[17]]))
        self.Edges.append(self.nodes[14].addEdge(self.nodes[18], "to", "IND_B1L4_CURRENT", [self.relays[18]]))
        self.Edges.append(self.nodes[14].addEdge(self.nodes[19], "to", "IND_B1L5_CURRENT", [self.relays[19]]))
        self.Edges.append(self.nodes[20].addEdge(self.nodes[21], "to", "IND_B2L1_CURRENT", [self.relays[21]]))
        self.Edges.append(self.nodes[20].addEdge(self.nodes[22], "to", "IND_B2L2_CURRENT", [self.relays[22]]))
        self.Edges.append(self.nodes[20].addEdge(self.nodes[23], "to", "IND_B2L3_CURRENT", [self.relays[23]]))
        self.Edges.append(self.nodes[20].addEdge(self.nodes[24], "to", "IND_B2L4_CURRENT", [self.relays[24]]))
        self.Edges.append(self.nodes[20].addEdge(self.nodes[25], "to", "IND_B2L5_CURRENT", [self.relays[25]]))             
        self.Edges.append(self.nodes[26].addEdge(self.nodes[27], "to", "RES_BUS1_CURRENT", [self.relays[27]]))
        self.Edges.append(self.nodes[26].addEdge(self.nodes[33], "to", "RES_BUS2_CURRENT", [self.relays[33]]))
        self.Edges.append(self.nodes[26].addEdge(self.nodes[39], "to", "RES_BUS3_CURRENT", [self.relays[39]]))
        self.Edges.append(self.nodes[26].addEdge(self.nodes[45], "to", "RES_BUS4_CURRENT", [self.relays[45]]))
        self.Edges.append(self.nodes[27].addEdge(self.nodes[28], "to", "RES_B1L1_CURRENT", [self.relays[28]]))
        self.Edges.append(self.nodes[26].addEdge(self.nodes[33], "to", "RES_BUS2_CURRENT", [self.relays[33]]))
        self.Edges.append(self.nodes[26].addEdge(self.nodes[39], "to", "RES_BUS3_CURRENT", [self.relays[39]]))
        self.Edges.append(self.nodes[26].addEdge(self.nodes[45], "to", "RES_BUS4_CURRENT", [self.relays[45]]))
        self.Edges.append(self.nodes[27].addEdge(self.nodes[28], "to", "RES_B1L1_CURRENT", [self.relays[28]]))
        self.Edges.append(self.nodes[27].addEdge(self.nodes[29], "to", "RES_B1L2_CURRENT", [self.relays[29]]))
        self.Edges.append(self.nodes[27].addEdge(self.nodes[30], "to", "RES_B1L3_CURRENT", [self.relays[30]]))
        self.Edges.append(self.nodes[27].addEdge(self.nodes[31], "to", "RES_B1L4_CURRENT", [self.relays[31]]))
        self.Edges.append(self.nodes[27].addEdge(self.nodes[32], "to", "RES_B1L5_CURRENT", [self.relays[32]]))
        self.Edges.append(self.nodes[33].addEdge(self.nodes[34], "to", "RES_B2L1_CURRENT", [self.relays[34]]))
        self.Edges.append(self.nodes[33].addEdge(self.nodes[35], "to", "RES_B2L2_CURRENT", [self.relays[35]]))
        self.Edges.append(self.nodes[33].addEdge(self.nodes[36], "to", "RES_B2L3_CURRENT", [self.relays[36]]))
        self.Edges.append(self.nodes[33].addEdge(self.nodes[37], "to", "RES_B2L4_CURRENT", [self.relays[37]]))
        self.Edges.append(self.nodes[33].addEdge(self.nodes[38], "to", "RES_B2L5_CURRENT", [self.relays[38]]))
        self.Edges.append(self.nodes[39].addEdge(self.nodes[40], "to", "RES_B3L1_CURRENT", [self.relays[40]]))
        self.Edges.append(self.nodes[39].addEdge(self.nodes[41], "to", "RES_B3L2_CURRENT", [self.relays[41]]))
        self.Edges.append(self.nodes[39].addEdge(self.nodes[42], "to", "RES_B3L3_CURRENT", [self.relays[42]]))
        self.Edges.append(self.nodes[39].addEdge(self.nodes[43], "to", "RES_B3L4_CURRENT", [self.relays[43]]))
        self.Edges.append(self.nodes[39].addEdge(self.nodes[44], "to", "RES_B3L5_CURRENT", [self.relays[44]]))
        self.Edges.append(self.nodes[45].addEdge(self.nodes[46], "to", "RES_B4L1_CURRENT", [self.relays[46]]))
        self.Edges.append(self.nodes[45].addEdge(self.nodes[47], "to", "RES_B4L2_CURRENT", [self.relays[47]]))
        self.Edges.append(self.nodes[45].addEdge(self.nodes[48], "to", "RES_B4L3_CURRENT", [self.relays[48]]))
        self.Edges.append(self.nodes[45].addEdge(self.nodes[49], "to", "RES_B4L4_CURRENT", [self.relays[49]]))
        self.Edges.append(self.nodes[45].addEdge(self.nodes[50], "to", "RES_B4L5_CURRENT", [self.relays[50]]))

        self.connMatrix = [[0 for x in range(len(self.nodes))] for y in range(len(self.nodes))]
        
       
        #import list of utility resources and make into object
        resource.makeResource(self.resources,self.Resources,False)
        for res in self.Resources:
            for node in self.nodes:
              if (res.location == node.name):
                 
                 node.addResource(res)
            self.dbnewresource(res,self.dbconn,self.t0)
        
        
        self.perceivedInsol = .75 #per unit
        self.customers = []
        self.DRparticipants = []
        
        #local storage to ease load on tag server
        self.tagCache = {}
        
        now = datetime.now()
        end = datetime.now() + timedelta(seconds = settings.ST_PLAN_INTERVAL)
        self.CurrentPeriod = control.Period(0,now,end,self)
        
        self.NextPeriod = control.Period(1,end,end + timedelta(seconds = settings.ST_PLAN_INTERVAL),self)
        
        self.bidstate = BidState()
        
        self.CurrentPeriod.printInfo(0)
        self.NextPeriod.printInfo(0)
        
        self.initnode()
        
    def initnode(self):
        self.relays[0].closeRelay()
        self.relays[1].closeRelay()
        self.relays[2].closeRelay()
        self.relays[3].closeRelay()
        self.relays[4].closeRelay()
        self.relays[5].closeRelay()
        self.relays[6].closeRelay()
        self.relays[7].closeRelay()
        self.relays[8].closeRelay()
        self.relays[9].closeRelay()
        self.relays[10].closeRelay()
        self.relays[11].closeRelay()
        self.relays[12].closeRelay()
        self.relays[13].closeRelay()
        self.relays[14].closeRelay()
        self.relays[15].closeRelay()
        self.relays[16].closeRelay()
        self.relays[17].closeRelay()
        self.relays[18].closeRelay()
        self.relays[19].closeRelay()
        self.relays[20].closeRelay()
        self.relays[21].closeRelay()
        self.relays[22].closeRelay()
        self.relays[23].closeRelay()
        self.relays[24].closeRelay()
        self.relays[25].closeRelay()
        self.relays[26].closeRelay()
        self.relays[27].closeRelay()
        self.relays[28].closeRelay()
        self.relays[29].closeRelay()
        self.relays[30].closeRelay()
        self.relays[31].closeRelay()
        self.relays[32].closeRelay()
        self.relays[33].closeRelay()
        self.relays[34].closeRelay()
        self.relays[35].closeRelay()
        self.relays[36].closeRelay()
        self.relays[37].closeRelay()
        self.relays[38].closeRelay()
        self.relays[39].closeRelay()
        self.relays[40].closeRelay()
        self.relays[41].closeRelay()
        self.relays[42].closeRelay()
        self.relays[43].closeRelay()
        self.relays[44].closeRelay()
        self.relays[45].closeRelay()
        self.relays[46].closeRelay()
        self.relays[47].closeRelay()
        self.relays[48].closeRelay()
        self.relays[49].closeRelay()
        self.relays[50].closeRelay()
        
       
        
    def exit_handler(self,dbconn):
        print('UTILITY {me} exit handler'.format(me = self.name))
        
        #disconnect any connected loads
        for cust in self.customers:
            cust.disconnectCustomer()
        
        #disconnect all utility-owned sources
        for res in self.Resources:
            res.disconnectSource()
        
        #close database connection
        dbconn.close()    
        
    @Core.receiver('onstart')
    def setup(self,sender,**kwargs):
        _log.info(self.config['message'])
        self._agent_id = self.config['agentid']
        self.state = "setup"
        
        self.vip.pubsub.subscribe('pubsub','energymarket', callback = self.marketfeed)
        self.vip.pubsub.subscribe('pubsub','demandresponse',callback = self.DRfeed)
        self.vip.pubsub.subscribe('pubsub','customerservice',callback = self.customerfeed)
        self.vip.pubsub.subscribe('pubsub','weatherservice',callback = self.weatherfeed)
        
        #self.printInfo(2)
              
        self.discoverCustomers()
        #solicit bids for next period, this function schedules a delayed function call to process
        #the bids it has solicited
        self.solicitBids()
        
        #schedule planning period advancement
        self.core.schedule(self.NextPeriod.startTime,self.advancePeriod)
        
        #schedule first customer enrollment attempt
        sched = datetime.now() + timedelta(seconds = 4)            
        delaycall = self.core.schedule(sched,self.discoverCustomers)
        
        #schedule bid solicitation for first period
        sched = datetime.now() + timedelta(seconds = 11)
        self.core.schedule(sched,self.sendBidSolicitation)
        
        subs = self.getTopology()
        if settings.DEBUGGING_LEVEL >= 2:
            print("UTILITY {me} THINKS THE TOPOLOGY IS {top}".format(me = self.name, top = subs))

    @Core.periodic(20)
    def getNowcast(self):
        mesdict = {}
        mesdict["message_sender"] = self.name
        mesdict["message_target"] = "Goddard"
        mesdict["message_subject"] = "nowcast"
        mesdict["message_type"] = "nowcast_request"
        #mesdict["requested_data"] = ["temperature"]
        
        mes = json.dumps(mesdict)
        self.vip.pubsub.publish("pubsub","weatherservice",{},mes)

    '''callback for weatherfeed topic'''
    def weatherfeed(self, peer, sender, bus, topic, headers, message):
        mesdict = json.loads(message)
        messageSubject = mesdict.get('message_subject',None)
        messageTarget = mesdict.get('message_target',None)
        messageSender = mesdict.get('message_sender',None)
        messageType = mesdict.get("message_type",None)
        #if we are the intended recipient
        if listparse.isRecipient(messageTarget,self.name):    
            if messageSubject == "nowcast":
                responses = mesdict.get("responses",None)
                if responses:
                    solar = responses["solar_irradiance"]
                    if solar:
                        self.perceivedInsol = solar

    '''callback for customer service topic. This topic is used to enroll customers
    and manage customer accounts.'''    
    def customerfeed(self, peer, sender, bus, topic, headers, message):
        #load json message
        try:
            mesdict = json.loads(message)
        except Exception as e:
            print("customerfeed message to {me} was not formatted properly".format(me = self))
        #determine intended recipient, ignore if not us    
        messageTarget = mesdict.get("message_target",None)
        if listparse.isRecipient(messageTarget,self.name):
            
            if settings.DEBUGGING_LEVEL >= 3:
                print(message)
            
            messageSubject = mesdict.get("message_subject",None)
            messageType = mesdict.get("message_type",None)
            messageSender = mesdict.get("message_sender",None)
            if messageSubject == "customer_enrollment":
                #if the message is a response to new customer solicitation
                if messageType == "new_customer_response":
                    if settings.DEBUGGING_LEVEL >= 2:
                        print("UTILITY {me} RECEIVED A RESPONSE TO CUSTOMER ENROLLMENT SOLICITATION FROM {them}".format(me = self.name, them = messageSender))
                    
                    name, location, resources, customerType = mesdict.get("info")                        
                    
                    #create a new object to represent customer in our database 
                    dupobj = listparse.lookUpByName(name,self.customers)
                    if dupobj:
                        if settings.DEBUGGING_LEVEL >= 2:
                            print("HOMEOWNER {me} has already registered {cust}".format(me = self.name, cust = name))
                        return
                    else:   
                        if customerType == "residential":
                            cust = customer.ResidentialCustomerProfile(name,location,resources,2)
                            self.customers.append(cust)
                        elif customerType == "commercial":
                            cust = customer.CommercialCustomerProfile(name,location,resources,5)
                            self.customers.append(cust)
                        elif customerType == "industrial":
                            cust = customer.IndustrialCustomerProfile(name,location,resources,5)
                            self.customers.append(cust)
                        
                        else:                        
                            if settings.DEBUGGING_LEVEL >= 2:
                                print("HOMEOWNER {me} doesn't recognize customer type".format(me = self.name))
                                return
                            
                        self.dbnewcustomer(cust,self.dbconn,self.t0)
                            
                        #add customer to Node object
                        for node in self.nodes:
                            if cust.location.split(".")== node.name.split("."):
                                newnode, newrelay, newedge = node.addCustomer(cust)
                                
                                #add new graph objects to lists - this causes problems because we can't measure voltage at loads
                                #self.nodes.append(newnode)
                                #self.relays.append(newrelay)
                                #self.Edges.append(newedge)
                                
                                if node.group:
                                    node.group.customers.append(cust)
                        
                        for resource in resources:
                            print("NEW RESOURCE: {res}".format(res = resource))
                            foundmatch = False
                            for node in self.nodes:
                                if node.name.split(".") == resource["location"].split("."):
                                    resType = resource.get("type",None)
                                    if resType == "LeadAcidBattery":
                                        newres = customer.LeadAcidBatteryProfile(**resource)
                                    elif resType == "ACresource":
                                        newres = customer.GeneratorProfile(**resource)
                                    else:
                                        print("unsupported resource type")
                                    node.addResource(newres)
                                    cust.addResource(newres)
                                    if node.group:
                                        node.group.resources.append(newres)
                                    foundmatch = True
                            if not foundmatch:
                                print("couldn't find a match for {loc}".format(loc = resource["location"]))
                        
                        
                            
                        if settings.DEBUGGING_LEVEL >= 1:
                            print("\nNEW CUSTOMER ENROLLMENT ##############################")
                            print("UTILITY {me} enrolled customer {them}".format(me = self.name, them = name))
                            cust.printInfo(0)
                            if settings.DEBUGGING_LEVEL >= 3:
                                print("...and here's how they did it:\n {mes}".format(mes = message))
                            print("#### END ENROLLMENT NOTIFICATION #######################")
                        
                        resdict = {}
                        resdict["message_subject"] = "customer_enrollment"
                        resdict["message_type"] = "new_customer_confirm"
                        resdict["message_target"] = name
                        response = json.dumps(resdict)
                        self.vip.pubsub.publish(peer = "pubsub",topic = "customerservice", headers = {}, message = response)
                        
                        if settings.DEBUGGING_LEVEL >= 1:
                            print("let the customer {name} know they've been successfully enrolled by {me}".format(name = name, me = self.name))
                            
                        #one more try
                        sched = datetime.now() + timedelta(seconds = 2)
                        self.core.schedule(sched,self.sendBidSolicitation)
                        
                    
            elif messageSubject == "request_connection":
                #the utility has the final say in whether a load can connect or not
                #look up customer object by name
                cust = listparse.lookUpByName(messageSender,self.customers)
                if cust.permission:
                    cust.connectCustomer()     
                    if settings.DEBUGGING_LEVEL >= 2:
                        print("{me} GRANTING CONNECTION REQUEST. {their} MAY CONNECT IN PERIOD {per}".format(me = self.name, their = messageSender, per = self.CurrentPeriod.periodNumber))
                else:
                    if settings.DEBUGGING_LEVEL >= 2:
                        print("{me} DENYING CONNECTION REQUEST. {their} HAS NO PERMISSION TO CONNECT IN PERIOD {per}".format(me = self.name, their = messageSender, per = self.CurrentPeriod.periodNumber))
            else:
                pass
    

    #called to send a DR enrollment message. when a customer has been enrolled
    #they can be called on to increase or decrease consumption to help the utility
    #meet its goals   
    def solicitDREnrollment(self, name = "broadcast"):
        mesdict = {}
        mesdict["message_subject"] = "DR_enrollment"
        mesdict["message_type"] = "enrollment_query"
        mesdict["message_target"] = name
        mesdict["message_sender"] = self.name
        mesdict["info"] = "name"
        
        message = json.dumps(mesdict)
        self.vip.pubsub.publish(peer = "pubsub", topic = "demandresponse", headers = {}, message = message)
        
        if settings.DEBUGGING_LEVEL >= 1:
            print("UTILITY {me} IS TRYING TO ENROLL {them} IN DR SCHEME".format(me = self.name, them = name))

    #the accountUpdate() function polls customer power consumption/production
    #and updates account balances according to their rate '''
    @Core.periodic(settings.ACCOUNTING_INTERVAL)
    def accountUpdate(self):
        #need more consideration
        print("UTILITY {me} ACCOUNTING ROUTINE".format(me = self.name))
        for group in self.groupList:
            for cust in group.customers:
                power = cust.measurePower()
                
                self.dbconsumption(cust,power,self.dbconn,self.t0)
                
                energy = power*settings.ACCOUNTING_INTERVAL
                balanceAdjustment = -energy*group.rate*cust.rateAdjustment
                if type(balanceAdjustment) is float or type(balanceAdjustment) is int:
                    if abs(balanceAdjustment) < 450 and abs(balanceAdjustment) > .001:
                        cust.customerAccount.adjustBalance(balanceAdjustment)
                        #update database
                        self.dbtransaction(cust,balanceAdjustment,"net home consumption",self.dbconn,self.t0)
                        if settings.DEBUGGING_LEVEL >= 2:
                            print("The account of {holder} has been adjusted by {amt} units for net home consumption".format(holder = cust.name, amt = balanceAdjustment))
                else:
                    print("HOMEOWNER {me} RECEIVED NaN FOR POWER MEASUREMENT".format(me = self.name))
                
                
            
            for res in group.resources:
                if res.owner != self.name:
                    
                    cust = listparse.lookUpByName(res.owner,self.customers)
                    
                    if cust:
                        if res.location != cust.location:
                            print("resource {res} not co-located with {cust}".format(res = res.name, cust = cust.name))
                            #if resources are not colocated, we need to account for them separately
                            power = res.getDischargePower() - res.getChargePower()
                            energy = power*settings.ACCOUNTING_INTERVAL
                            balanceAdjustment = energy*group.rate*cust.rateAdjustment
                            
                            if type(balanceAdjustment) is float or type(balanceAdjustment) is int:
                                if abs(balanceAdjustment) < 450 and abs(balanceAdjustment) > .001:
                                    cust.customerAccount.adjustBalance(balanceAdjustment)
                            
                                    #update database
                                    self.dbtransaction(cust,balanceAdjustment,"remote resource",self.dbconn,self.t0)
                            
                        else:
                            print("TEMP DEBUG: resource {res} is co-located with {cust}".format(res = res.name, cust = cust.name))
                    else:
                        print("TEMP-DEBUG: can't find owner {own} for {res}".format(own = res.owner, res = res.name))

             
    
    
    
    
    
    
    
    @Core.periodic(settings.LT_PLAN_INTERVAL)
    def planLongTerm(self):
        pass

    @Core.periodic(settings.ANNOUNCE_PERIOD_INTERVAL)
    def announcePeriod(self):    
        mesdict = {"message_sender" : self.name,
                   "message_target" : "broadcast",
                   "message_subject" : "announcement",
                   "message_type" : "period_announcement",
                   "period_number" : self.NextPeriod.periodNumber,
                   "start_time" : self.NextPeriod.startTime.isoformat(),
                   "end_time" : self.NextPeriod.endTime.isoformat()
                   }
        
        if settings.DEBUGGING_LEVEL >= 2:
            print("UTILITY {me} ANNOUNCING period {pn} starting at {t}".format(me = self.name, pn = mesdict["period_number"], t = mesdict["start_time"]))
        message = json.dumps(mesdict)
        self.vip.pubsub.publish("pubsub","energymarket",{},message)

    def announceRate(self, recipient, rate, period):
        if settings.DEBUGGING_LEVEL >= 2:
            print("UTILITY {me} ANNOUNCING RATE {rate} to {rec} for period {per}".format(me = self.name, rate = rate, rec = recipient.name, per = period.periodNumber))
        mesdict = {"message_sender" : self.name,
                   "message_subject" : "rate_announcement",
                   "message_target" : recipient.name,
                   "period_number" : period.periodNumber,
                   "rate" : rate
                   }
        message = json.dumps(mesdict)
        self.vip.pubsub.publish("pubsub","energymarket",{},message)

     #solicit bids for the next period
    def solicitBids(self):
        
        subs = self.getTopology()
        self.printInfo(2)
        if settings.DEBUGGING_LEVEL >= 2:
            print("UTILITY {me} THINKS THE TOPOLOGY IS {top}".format(me = self.name, top = subs))
        
        self.announceTopology()
        
        #first we have to find out how much it will cost to get power
        #from various sources, both those owned by the utility and by 
        #customers
        
        #clear the bid list in preparation for receiving new bids
        self.supplyBidList = []
        self.reserveBidList = []
        self.demandBidList = []
        
        #send bid solicitations to all customers who are known to have resources
        self.sendBidSolicitation()
        
        sched = datetime.now() + timedelta(seconds = settings.BID_SUBMISSION_INTERVAL)            
        delaycall = self.core.schedule(sched,self.planShortTerm)
        
    #sends bid solicitation without rescheduling call to planning function or finding topology
    def sendBidSolicitation(self):
        if settings.DEBUGGING_LEVEL >=2 :
            print("\nUTILITY {me} IS ASKING FOR BIDS FOR PERIOD {per}".format(me = self.name, per = self.NextPeriod.periodNumber))
        
        self.bidstate.acceptall()
        for group in self.groupList:
            #group.printInfo()
            for cust in group.customers:
                #cust.printInfo()
                # ask about consumption
                mesdict = {}
                mesdict["message_sender"] = self.name
                mesdict["message_subject"] = "bid_solicitation"
                mesdict["side"] = "demand"
                mesdict["message_target"] = cust.name
                mesdict["period_number"] = self.NextPeriod.periodNumber
                mesdict["solicitation_id"] = self.uid
                self.uid += 1
                
                mess = json.dumps(mesdict)
                self.vip.pubsub.publish(peer = "pubsub", topic = "energymarket", headers = {}, message = mess)
                    
                if settings.DEBUGGING_LEVEL >= 2:
                    print("UTILITY {me} SOLICITING CONSUMPTION BIDS FROM {them}".format(me = self.name, them = cust.name))
                    
                
                if cust.resources:
                    #ask about bulk power
                    mesdict = {}
                    mesdict["message_sender"] = self.name
                    mesdict["message_subject"] = "bid_solicitation"
                    mesdict["side"] = "supply"
                    mesdict["service"] = "power"
                    mesdict["message_target"] = cust.name
                    mesdict["period_number"] = self.NextPeriod.periodNumber
                    mesdict["solicitation_id"] = self.uid
                    self.uid += 1
                    
                    mess = json.dumps(mesdict)
                    self.vip.pubsub.publish(peer = "pubsub", topic = "energymarket", headers = {}, message = mess)
                    
                    if settings.DEBUGGING_LEVEL >= 2:
                        print("UTILITY {me} SOLICITING BULK POWER BIDS FROM {them}".format(me = self.name, them = cust.name))
                    
                    #ask about reserves                    
                    mesdict["solicitation_id"] = self.uid
                    mesdict["service"] = "reserve"
                    self.uid += 1
                    
                    mess = json.dumps(mesdict)
                    self.vip.pubsub.publish(peer = "pubsub", topic = "energymarket", headers = {}, message = mess)
                    
                    if settings.DEBUGGING_LEVEL >= 2:
                        print("UTILITY {me} SOLICITING RESERVE POWER BIDS FROM {them}".format(me = self.name, them = cust.name))

    def planShortTerm(self):
        if settings.DEBUGGING_LEVEL >= 2:
            print("\nUTILITY {me} IS FORMING A NEW SHORT TERM PLAN FOR PERIOD {per}".format(me = self.name,per = self.NextPeriod.periodNumber))
        
        #tender bids for the utility's own resources

#need more consideration!!!





#rate for two resources need more consideration
#from demand equation, rate should have negative relation with demand amount 
#how totalsupply comes? it comes from bids.amount and how it 
        
       
        for res in self.Resources:
            newbid = None
           
            if type(res) is resource.ACresource:
                amount = res.maxDischargePower*.8
                rate = 1.2*res.fuelCost + 0.01*random.randint(0,9)
                newbid = control.SupplyBid(**{"resource_name": res.name, "side":"supply", "service":"power", "amount": amount, "rate":rate, "counterparty":self.name, "period_number": self.NextPeriod.periodNumber})
                if newbid:
                    print("UTILITY {me} ADDING OWN BID {id} TO LIST".format(me = self.name, id = newbid.uid))
                    self.supplyBidList.append(newbid)
                    self.outstandingSupplyBids.append(newbid)
                    
                    #write to database
                    self.dbnewbid(newbid,self.dbconn,self.t0)
            
            elif type(res) is resource.LeadAcidBattery:
                amount = res.maxDischargePower
                rate = max(control.ratecalc(res.capCost,.05,res.amortizationPeriod,.05),res.capCost/res.cyclelife) + 0.005*amount + 0.01*random.randint(0,9)
                newbid = control.SupplyBid(**{"resource_name": res.name, "side":"supply", "service":"reserve", "amount": amount, "rate":rate, "counterparty": self.name, "period_number": self.NextPeriod.periodNumber})
                if newbid:
                    print("UTILITY {me} ADDING OWN BID {id} TO LIST".format(me = self.name, id = newbid.uid))
                    self.reserveBidList.append(newbid)
                    
                    #write to database
                    self.dbnewbid(newbid,self.dbconn,self.t0)
            
            else:
                print("trying to plan for an unrecognized resource type")
            
            
        for group in self.groupList:
            #??how to get total power of every load 
            maxLoad = 0
            for bid in self.demandBidList:
                maxLoad += bid.amount
            print("maxLoad:{maxLoad}".format(maxLoad = maxLoad))  
                   
            #sort array of supplier bids by rate from low to high
            self.supplyBidList.sort(key = operator.attrgetter("rate"))
            #sort array of consumer bids by rate from high to low
            self.demandBidList.sort(key = operator.attrgetter("rate"),reverse = True)
                   
            if settings.DEBUGGING_LEVEL >= 2:
                print("\n\nPLANNING for GROUP {group} for PERIOD {per}: worst case load is {max}".format(group = group.name, per = self.NextPeriod.periodNumber, max = maxLoad))
                print(">>here are the supply bids:")
                for bid in self.supplyBidList:                    
                    bid.printInfo(0)
                print(">>here are the reserve bids:")
                for bid in self.reserveBidList:                    
                    bid.printInfo(0)          
                print(">>here are the demand bids:")          
                for bid in self.demandBidList:                    
                    bid.printInfo(0)
            
            qrem = 0                #leftover part of bid
            supplyindex = 0
            demandindex = 0
            partialsupply = False
            partialdemand = False
            sblen = len(self.supplyBidList)
            rblen = len(self.reserveBidList)
            dblen = len(self.demandBidList)
            
            
            while supplyindex < sblen and demandindex < dblen:
                
                supbid = self.supplyBidList[supplyindex]
                dembid = self.demandBidList[demandindex]
                
                print("supplybid rate: {sup}".format(sup = supbid.rate))
                print("demandbid rate: {dem}".format(dem = dembid.rate))
                if settings.DEBUGGING_LEVEL >= 2:
                    print("\ndemand index: {di}".format(di = demandindex))
                    print("supply index: {si}".format(si = supplyindex))
                    
                if dembid.rate >= supbid.rate:
                    if settings.DEBUGGING_LEVEL >= 2:
                        print("demand rate {dr} > supply rate {sr}".format(dr = dembid.rate, sr = supbid.rate))
                        
                    group.rate = dembid.rate
                    if partialsupply:
                        if settings.DEBUGGING_LEVEL >= 2:
                            print("partial supply bid: {qr} remaining".format(qr = qrem))
                        
                        if qrem > dembid.amount:                            
                            qrem -= dembid.amount
                            if settings.DEBUGGING_LEVEL >= 2:
                                print("still {qr} remaining in supply bid".format(qr = qrem))
                            partialsupply = True
                            partialdemand = False
                            dembid.accepted = True
                            demandindex += 1
                        elif qrem < dembid.amount:        
                            qrem = dembid.amount - qrem
                            if settings.DEBUGGING_LEVEL >= 2:
                                print("exhausted supply bid, now {qr} left in demand bid".format(qr = qrem))
                            partialsupply = False
                            partialdemand = True
                            supbid.accepted = True
                            supplyindex += 1                            
                        else:
                            if settings.DEBUGGING_LEVEL >= 2:
                                print("exact match in bids")
                            qrem = 0
                            partialsupply = False
                            partialdemand = False     
                            supbid.accepted = True   
                            dembid.accepted = True 
                            supplyindex += 1
                            demandindex += 1       
                    elif partialdemand:
                        if settings.DEBUGGING_LEVEL >= 2:
                            print("partial demand bid: {qr} remaining".format(qr = qrem))
                            
                        if qrem > supbid.amount:
                            qrem -= supbid.amount
                            if settings.DEBUGGING_LEVEL >= 2:
                                print("still {qr} remaining in supply bid".format(qr = qrem))
                            partialsupply = False
                            partialdemand = True
                            supbid.accepted = True
                            supplyindex += 1
                        elif qrem < supbid.amount:
                            qrem = supbid.amount - qrem
                            if settings.DEBUGGING_LEVEL >= 2:
                                print("exhausted demand bid, now {qr} left in supply bid".format(qr = qrem))
                            partialsupply = True
                            partialdemand = False
                            dembid.accepted = True
                            demandindex += 1
                        else:
                            if settings.DEBUGGING_LEVEL >= 2:
                                print("exact match in bids")
                            qrem = 0
                            partialsupply = False
                            partialdemand = False
                            supbid.accepted = True   
                            dembid.accepted = True 
                            supplyindex += 1
                            demandindex += 1
                    else:
                        if settings.DEBUGGING_LEVEL >= 2:
                                print("no partial bids")
                                
                        if dembid.amount > supbid.amount:
                            qrem = dembid.amount - supbid.amount
                            if settings.DEBUGGING_LEVEL >= 2:
                                print("{qr} remaining in demand bid".format(qr = qrem))
                            partialdemand = True
                            partialsupply = False
                            supbid.accepted = True
                            dembid.accepted = True
                            supplyindex += 1
                        elif dembid.amount < supbid.amount:
                            qrem = supbid.amount - dembid.amount
                            if settings.DEBUGGING_LEVEL >= 2:
                                print("{qr} remaining in supply bid".format(qr = qrem))
                            partialdemand = False
                            partialsupply = True
                            supbid.accepted = True
                            dembid.accepted = True
                            demandindex += 1
                        else:
                            if settings.DEBUGGING_LEVEL >= 2:
                                print("bids match exactly")
                            qrem = 0
                            partialsupply = False
                            partialdeand = False
                            supbid.accepted = True
                            dembid.accepted = True
                            supplyindex += 1
                            demandindex += 1
                        
                else:
                    if settings.DEBUGGING_LEVEL >= 2:
                        print("PAST EQ PRICE! demand rate {dr} < supply rate {sr}".format(dr = dembid.rate, sr = supbid.rate))
                    if partialsupply:
                        if settings.DEBUGGING_LEVEL >= 2:
                            print("still partial supply bid to take care of")
                        supbid.accepted = True
                        supbid.modified = True
                        supbid.amount -= qrem
                        dembid.accepted = False
                        partialsupply = False
                        partialdemand = False
                    elif partialdemand:
                        if settings.DEBUGGING_LEVEL >= 2:
                            print("still partial demand bid to take care of")
                        dembid.accepted = True
                        dembid.modified = True
                        dembid.amount -= qrem
                        supbid.accepted = False
                        partialsupply = False
                        partialdemand = False
                    else:
                        if settings.DEBUGGING_LEVEL >= 2:
                            print("reject and skip...")
                        supbid.accepted = False
                        dembid.accepted = False
                    supplyindex += 1
                    demandindex += 1
            
            while supplyindex < sblen:
                supbid = self.supplyBidList[supplyindex]
                if settings.DEBUGGING_LEVEL >= 2:
                    print(" out of loop, still cleaning up supply bids {si}".format(si = supplyindex))
                if partialsupply:
                    if settings.DEBUGGING_LEVEL >= 2:
                        print("partial supply bid to finish up")
                    supbid.accepted = True
                    supbid.modified = True
                    supbid.amount -= qrem
                    partialsupply = False
                    partialdemand = False
                else:
                    if supbid.auxilliaryService:
                        if supbid.auxilliaryService == "reserve":
                            if settings.DEBUGGING_LEVEL >= 2:
                                print("UTILITY {me} placing rejected power bid {bid} in reserve list".format(me = self.name, bid = supbid.uid))
                                
                            
                            self.supplyBidList.remove(supbid)
                            sblen = len(self.supplyBidList)
                            self.reserveBidList.append(supbid)
                            supbid.service = "reserve"
                    else:
                        supbid.accepted = False
                supplyindex += 1
                
            while demandindex < dblen:
                dembid = self.demandBidList[demandindex]
                if settings.DEBUGGING_LEVEL >= 2:
                    print(" out of loop, still cleaning up demand bids {di}".format(di = demandindex))
                if partialdemand:
                    if settings.DEBUGGING_LEVEL >= 2:
                        print("partial demand bid to finish up")
                    dembid.accepted = True
                    dembid.modified = True
                    dembid.amount -= qrem
                    partialsupply = False
                    partialdemand = False
                else:
                    dembid.accepted = False
                demandindex += 1
            
            totalsupply = 0
            #notify the counterparties of the terms on which they will supply power
            for bid in self.supplyBidList:
                if bid.accepted:
                    totalsupply += bid.amount
                    bid.rate = group.rate
                    self.sendBidAcceptance(bid, group.rate)
                    #update bid's entry in database
                    self.dbupdatebid(bid,self.dbconn,self.t0)
                    
                    #self.NextPeriod.plan.addBid(bid)
                    self.NextPeriod.supplybidmanager.acceptedbids.append(bid)
                    
                    
                    #give customer permission to connect if resource is co-located
                    res = listparse.lookUpByName(bid.resourceName,group.resources)
                    cust = listparse.lookUpByName(bid.counterparty,self.customers)
                    if cust:
                        if res.location == cust.location:
                            cust.permission = True   
                else:
                    self.sendBidRejection(bid, group.rate)   
                    #update bid's entry in database
                    self.dbupdatebid(bid,self.dbconn,self.t0)
                    
            totaldemand = 0        
            #notify the counterparties of the terms on which they will consume power
            for bid in self.demandBidList:
                #look up customer object corresponding to bid
                cust = listparse.lookUpByName(bid.counterparty,self.customers)
                if bid.accepted:
                    totaldemand += bid.amount
                    bid.rate = group.rate
                    self.sendBidAcceptance(bid, group.rate)
                    #update bid's entry in database
                    self.dbupdatebid(bid,self.dbconn,self.t0)
                    
                    #self.NextPeriod.plan.addConsumption(bid)
                    self.NextPeriod.demandbidmanager.readybids.append(bid)
                    
                    #give customer permission to connect
                    cust.permission = True                    
                    
                else:
                    self.sendBidRejection(bid, group.rate)
                    #update bid's entry in database
                    self.dbupdatebid(bid,self.dbconn,self.t0)
                    #customer does not have permission to connect
                    cust.permission = False
            
            self.reserveBidList.sort(key = operator.attrgetter("rate"))
            totalreserve = 0
            leftbidlist = []         
            for bid in self.reserveBidList:
                print("maxLoad ({ml})- totalsupply({ts}): {tr}".format( ml = maxLoad,ts = totalsupply, tr = maxLoad-totalsupply))
                if totalreserve < (maxLoad - totalsupply) and (maxLoad - totalsupply) > 0:
                    totalreserve += bid.amount
                    print("totalreserve = {tr}".format(tr = totalreserve))
                    
                    for leftbid in self.demandBidList:
                        print("leftbid in demandBidlist")
                        if leftbid.accepted == 0:
                            leftbidlist.append(leftbid) 
                            print("create leftbid list")
                            
                    print("leftbidlist: {lb}".format(lb=leftbidlist))       
                    leftbidlist.sort(key=operator.attrgetter("rate"),reverse = True)
                    leftlen = len(leftbidlist)
                    leftindex = 0
                    partialreserve = False
                    qrem = bid.amount
                    print("leftindex: {li}".format(li=leftindex))
                    print("leftlen: {len}".format(len=leftlen))
                    while leftindex<leftlen:    
                        leftbid = leftbidlist[leftindex]
                        print("leftbid:")  
                        leftbid.printInfo()                     
                        if bid.rate < leftbid.rate:
                            group.rate = leftbid.rate
                            print("reserve bid rate < leftbid rate")                            
                            if qrem > leftbid.amount:
                                qrem -= leftbid.amount
                                leftbid.accepted = True
                                leftindex += 1
                                print("reserve still left")
                            else:
                                if qrem > 0:
                                    leftbid.accepted = True
                                    leftbid.amount = qrem
                                    qrem = 0
                                    leftindex += 1
                                    print("partial reserve")
                                else:
                                    leftbid.accepted = False
                                    leftindex += 1
                                    print("reserve is used up")
                        else:
                            leftbid.accepted = False
                            leftindex += 1
                    bid.amount = bid.amount - qrem 
                    if bid.amount != 0:
                        bid.accepted = True               
                        print("reserve bid accepted")
                        print("bid amount = {ba}".format(ba = bid.amount))
                                        
                        self.sendBidAcceptance(leftbid, leftbid.rate)
                        #update bid's entry in database
                        self.dbupdatebid(leftbid,self.dbconn,self.t0)
                                    
                        #self.NextPeriod.plan.addConsumption(bid)
                        self.NextPeriod.demandbidmanager.readybids.append(leftbid)
                                        
                        #give customer permission to connect
                        cust.permission = True 
                                          
                    else:
                        bid.accepted = False                 
                else: 
                    bid.accepted = False
                    
                    
            for bid in self.reserveBidList:
                if bid.accepted:
                    self.sendBidAcceptance(bid,group.rate)
                    
                    #update bid's entry in database
                    self.dbupdatebid(bid,self.dbconn,self.t0)
                    
                    #self.NextPeriod.plan.addBid(bid)
                    self.NextPeriod.supplybidmanager.readybids.append(bid)
                else:
                    self.sendBidRejection(bid,group.rate)
                #update bid's entry in database
                self.dbupdatebid(bid,self.dbconn,self.t0)
                    
            self.bidstate.reserveonly()
            
                                   
            #announce rates for next period
            for cust in group.customers:
                self.announceRate(cust,group.rate,self.NextPeriod)
        
        
        for plan in self.NextPeriod.plans:
            self.NextPeriod.plan.printInfo(0)



        def sendDR(self,target,type,duration):
            mesdict = {"message_subject" : "DR_event",
                       "message_sender" : self.name,
                       "message_target" : target,
                       "event_id" : random.getrandbits(32),
                       "event_duration": duration,
                       "event_type" : type
                        }
            message = json.dumps(mesdict)
            self.vip.pubsub.publish("pubsub","demandresponse",{},message)

        '''scheduled initially in init, the advancePeriod() function makes the period for
        which we were planning into the period whose plan we are carrying out at the times
        specified in the period objects. it schedules another call to itself each time and 
        also runs the enactPlan() function to actuate the planned actions for the new
        planning period ''' 


    def advancePeriod(self):
        self.bidstate.acceptnone()
        #make next period the current period and create new object for next period
        self.CurrentPeriod = self.NextPeriod
        self.NextPeriod = control.Period(self.CurrentPeriod.periodNumber+1,self.CurrentPeriod.endTime,self.CurrentPeriod.endTime + timedelta(seconds = settings.ST_PLAN_INTERVAL),self)
        
        if settings.DEBUGGING_LEVEL >= 1:
            print("UTILITY AGENT {me} moving into new period:".format(me = self.name))
            self.CurrentPeriod.printInfo(0)
        
        #call enactPlan
        self.enactPlan()
        
        #solicit bids for next period, this function schedules a delayed function call to process
        #the bids it has solicited
        self.solicitBids()
                
        #schedule next advancePeriod call
        self.core.schedule(self.NextPeriod.startTime,self.advancePeriod)
        self.announcePeriod()
        
        #determine distribution system efficiency
        #self.efficiencyAssessment()
        
        #reset customer permissions
        #for cust in self.customers:
        #    cust.permission = False
        
        #responsible for enacting the plan which has been defined for a planning period
    def enactPlan(self):
        #which resources are being used during this period? keep track with this list
        involvedResources = []
        #change setpoints
        
        #if self.CurrentPeriod.plans:
        if self.CurrentPeriod.supplybidmanager.acceptedbids:
            #plan = self.CurrentPeriod.plans[0]
            if settings.DEBUGGING_LEVEL >= 2:
                print("UTILITY {me} IS ENACTING ITS PLAN FOR PERIOD {per}".format(me = self.name, per = self.CurrentPeriod.periodNumber))
            
            self.CurrentPeriod.supplybidmanager.printInfo()    
            for bid in self.CurrentPeriod.supplybidmanager.acceptedbids:
                if bid.counterparty == self.name:                    
                    if settings.DEBUGGING_LEVEL >= 2:
                        print("UTILITY {me} IS ACTUATING BID {bid}".format(me = self.name, bid = bid.uid))
                    
                    bid.printInfo(0)
                    res = listparse.lookUpByName(bid.resourceName,self.Resources)
                    if res is not None:
                        involvedResources.append(res)
                        #if the resource is already connected, change the setpoint
                        if res.connected == True:
                            if settings.DEBUGGING_LEVEL >= 2:
                                print(" Resource {rname} is already connected".format(rname = res.name))
                            if bid.service == "power":
                                #res.DischargeChannel.ramp(bid.amount)
                                #res.DischargeChannel.changeSetpoint(bid.amount)
                                res.setDisposition(bid.amount, 0)
                                if settings.DEBUGGING_LEVEL >= 2:
                                    print("Power resource {rname} setpoint to {amt}".format(rname = res.name, amt = bid.amount))
                            elif bid.service == "reserve":
                                #res.DischargeChannel.ramp(.1)            
                                #res.DischargeChannel.changeReserve(bid.amount,-.2)
                                print("res.name: {name}".format(name = res.name))
                                res.setDisposition(bid.amount,-0.2)
                                if settings.DEBUGGING_LEVEL >= 2:
                                    print("Reserve resource {rname} setpoint to {amt}".format(rname = res.name, amt = bid.amount))
                        #if the resource isn't connected, connect it and ramp up power
                        else:
                            if bid.service == "power":
                                #res.connectSourceSoft("Preg",bid.amount)
                                #res.DischargeChannel.connectWithSet(bid.amount,0)
                                res.setDisposition(bid.amount,0)
                                if settings.DEBUGGING_LEVEL >= 2:
                                    print("Connecting resource {rname} with setpoint: {amt}".format(rname = res.name, amt = bid.amount))
                            elif bid.service == "reserve":
                                #res.connectSourceSoft("Preg",.1)
                                #res.DischargeChannel.connectWithSet(bid.amount, -.2)
                                res.setDisposition(bid.amount, -0.2)
                                if settings.DEBUGGING_LEVEL >= 2:
                                    print("Committed resource {rname} as a reserve with setpoint: {amt}".format(rname = res.name, amt = bid.amount))


                            
                           
            #disconnect resources that aren't being used anymore
            for res in self.Resources:
                if res not in involvedResources:
                    if res.connected == True:
                        #res.disconnectSourceSoft()
                        res.DischargeChannel.disconnect()
                        if settings.DEBUGGING_LEVEL >= 2:
                            print("Resource {rname} no longer required and is being disconnected".format(rname = res.name))

                           
                     
        
    def groundFaultHandler(self,*argv):
        fault = argv[0]
        zone = argv[1]
        if fault is None:
            fault = zone.newGroundFault()
            if settings.DEBUGGING_LEVEL >= 2:
                fault.printInfo()
            
        if fault.state == "suspected":
            iunaccounted = zone.sumCurrents()
            if abs(iunaccounted) > .1:
                #pick a node to isolate first - lowest priority first
                zone.rebuildpriorities()
                selnode = zone.nodeprioritylist[0]
                
                fault.isolatenode(selnode)
                
                if settings.DEBUGGING_LEVEL >= 1:
                    print("FAULT: unaccounted current {cur} indicates ground fault({sta}). Isolating node {nod}".format(cur = iunaccounted, sta = fault.state, nod = selnode.name))
                
                #update fault state
                fault.state = "unlocated"
                #reschedule ground fault handler
                schedule.msfromnow(self,60,self.groundFaultHandler,fault,zone)
            else:
                #no problem
                 
                if settings.DEBUGGING_LEVEL >= 1:
                    print("FAULT: suspected fault resolved")
                
                fault.cleared()
               
                            
        elif fault.state == "unlocated":
            #check zone to see if fault condition persists
            iunaccounted = zone.sumCurrents()
            if abs(iunaccounted) > .1:
                zone.rebuildpriorities()
                for node in zone.nodeprioritylist:
                    if node not in fault.isolatednodes:
                        selnode = node
                        break
                if settings.DEBUGGING_LEVEL >= 1:
                    print("FAULT: unaccounted current of {cur} indicates ground fault still unlocated. Isolating node {sel}".format(cur = iunaccounted, sel = selnode.name))
                    if settings.DEBUGGING_LEVEL >= 2:
                        fault.printInfo()
                            
                fault.isolatenode(selnode)
                            
                #reschedule ground fault handler
                schedule.msfromnow(self,60,self.groundFaultHandler,fault,zone)
                
            else:
                #the previously isolated node probably contained the fault
                faultednode = fault.isolatednodes[-1]
                fault.faultednodes.append(faultednode)
                
                fault.state == "located"
                #nodes in zone that are not marked faulted can be restored
                for node in zone.nodes:
                    if node not in fault.faultednodes:
                        fault.restorenode(node)
                        
                if settings.DEBUGGING_LEVEL >= 1:
                    print("FAULT: located at {nod}. restoring other unfaulted nodes".format(nod = faultednode))
                    if settings.DEBUGGING_LEVEL >= 2:
                        fault.printInfo()
                        
                #reschedule
                schedule.msfromnow(self,100,self.groundFaultHandler,fault,zone)
                
        elif fault.state == "located":
            #at least one faulted node has been located and isolated but there may be others
            if abs(zone.sumCurrents()) > .1:
                #there is another faulted node, go back and find it
                fault.state = "unlocated"
                
                if settings.DEBUGGING_LEVEL >= 1:
                    print("FAULT: there are multiple faults in this zone. go back and find some more.")
                    if settings.DEBUGGING_LEVEL >= 2:
                        fault.printInfo()
                
                self.groundFaultHandler(fault,zone)
            else:
                if settings.DEBUGGING_LEVEL >= 1:
                    print("FAULT: looks like we've isolated all faulted nodes and only faulted nodes.")
                
                #we seem to have isolated the faulted node(s)
                if fault.reclose:
                    fault.state = "reclose"
                    if settings.DEBUGGING_LEVEL >= 1:
                        print("FAULT: going to reclose. count: {rec}".format(rec = fault.reclosecounter))
                else:
                    #our reclose limit has been met
                    fault.state = "persistent"
                    if settings.DEBUGGING_LEVEL >= 1:
                        print("FAULT: no more reclosing, fault is persistent.")
                
                #schedule next call
                schedule.msfromnow(self,600,self.groundFaultHandler,fault,zone)
        elif fault.state == "reclose":
            if settings.DEBUGGING_LEVEL >= 1:
                print("reclosing")
                
            for node in zone:
                fault.reclosenode()
            fault.state = "suspected"
            schedule.msfromnow(self,100,self.groundFaultHandler,fault,zone)
        elif fault.state == "persistent":
            #fault hasn't resolved on its own, need to send a crew to clear fault
            pass
        
        elif fault.state == "cleared":
            fault.cleared()
            if settings.DEBUGGING_LEVEL >= 2:
                print("GROUND FAULT {id} has been cleared".format(id = fault.uid))
        else:
            print("Error, unrecognized fault state in {id}: {state}".format(id = fault.uid, state = fault.state))

#delete the part of fault handler
#delete the part of current and voltage monitor  
 
    def sendBidAcceptance(self,bid,rate):
        mesdict = {}
        mesdict["message_subject"] = "bid_acceptance"
        mesdict["message_target"] = bid.counterparty
        mesdict["message_sender"] = self.name
        
        mesdict["amount"] = bid.amount
        
        if bid.__class__.__name__ == "SupplyBid":
            mesdict["side"] = bid.side
            mesdict["service"] = bid.service
        elif bid.__class__.__name__ == "DemandBid":
            mesdict["side"] = bid.side
        else:
            mesdict["side"] = "unspecified"
            

            
            
        mesdict["rate"] = rate        
        mesdict["period_number"] = bid.periodNumber
        mesdict["uid"] = bid.uid
        
        if settings.DEBUGGING_LEVEL >= 2:
            print("UTILITY AGENT {me} sending bid acceptance to {them} for {uid}".format(me = self.name, them = bid.counterparty, uid = bid.uid))
        
        mess = json.dumps(mesdict)
        self.vip.pubsub.publish(peer = "pubsub",topic = "energymarket",headers = {}, message = mess)
        
    def sendBidRejection(self,bid,rate):
        mesdict = {}
        mesdict["message_subject"] = "bid_rejection"
        mesdict["message_target"] = bid.counterparty
        mesdict["message_sender"] = self.name
        
        mesdict["amount"] = bid.amount
        mesdict["rate"] = rate        
        if bid.__class__.__name__ == "SupplyBid":
            mesdict["side"] = "supply"
            mesdict["service"] = bid.service
        elif bid.__class__.__name__ == "DemandBid":
            mesdict["side"] = "demand"
        else:
            mesdict["side"] = "unspecified"
        mesdict["period_number"] = bid.periodNumber
        mesdict["uid"] = bid.uid
        
        if settings.DEBUGGING_LEVEL >= 2:
            print("UTILITY AGENT {me} sending bid rejection to {them} for {uid}".format(me = self.name, them = bid.counterparty, uid = bid.uid))
        
        mess = json.dumps(mesdict)
        self.vip.pubsub.publish(peer = "pubsub",topic = "energymarket",headers = {}, message = mess)
    
    '''solicit participation in DR scheme from all customers who are not
    currently participants'''
    @Core.periodic(settings.DR_SOLICITATION_INTERVAL)
    def DREnrollment(self):
        if settings.DEBUGGING_LEVEL >= 2:
            print("\nUTILITY {me} TRYING TO ENROLL CUSTOMERS IN A DR SCHEME".format(me = self.name))
        for entry in self.customers:
            if entry.DRenrollee == False:
                self.solicitDREnrollment(entry.name)
    
    '''broadcast message in search of new customers'''
    @Core.periodic(settings.CUSTOMER_SOLICITATION_INTERVAL)
    def discoverCustomers(self):
        if settings.DEBUGGING_LEVEL >= 2:
            print("\nUTILITY {me} TRYING TO FIND CUSTOMERS".format(me = self.name))
        mesdict = self.standardCustomerEnrollment
        mesdict["message_sender"] = self.name
        message = json.dumps(mesdict)
        self.vip.pubsub.publish(peer = "pubsub", topic = "customerservice", headers = {}, message = message)
        
        if settings.DEBUGGING_LEVEL >= 1:
            print(message)
            
    '''find out how much power is available from utility owned resources for a group at the moment'''    
    def getAvailableGroupPower(self,group):
        #first check to see what the grid topology is
        total = 0
        for elem in group.resources:
            if elem is LeadAcidBattery:
                if elem.SOC < .2:
                    total += 0
                elif elem.SOC > .4:
                    total += 20
            else:
                pass
        return total
    
            
    def getAvailableGroupDR(self,group):
        pass

    def getMaxGroupLoad(self,group):
        #print("MAX getting called for {group}".format(group = group.name))
        total = 0
        #print(group.customers)
        for load in group.customers:
            total += load.maxDraw
            #print("adding {load} to max load".format(load = load.maxDraw))
        return total
    
    ''' assume that power consumption won't change much between one short term planning
    period and the next'''
    def getExpectedGroupLoad(self,group):
        #print("EXP getting called for {group}".format(group = group.name))
        total = 0
        #print(group.customers)
        for load in group.customers:
            total += load.getPower()
            #print("adding {load} to expected load".format(load = load.getPower()))
        return total
    
    '''update agent's knowledge of the current grid topology'''
    def getTopology(self):
        self.rebuildConnMatrix()
        subs = graph.findDisjointSubgraphs(self.connMatrix)
        if len(subs) >= 1:
            del self.groupList[:]
            for i in range(1,len(subs)+1):
                #create a new group class for each disjoint subgraph
                self.groupList.append(groups.Group("group{i}".format(i = i),[],[],[]))
            for index,sub in enumerate(subs):
                #for concision
                cGroup = self.groupList[index]
                for node in sub:
                    cNode = self.nodes[node]
                    cGroup.addNode(cNode)
        else:
            print("got a weird number of disjoint subgraphs in utilityagent.getTopology()")
            
        self.dbtopo(str(subs),self.dbconn,self.t0)
        
        return subs
    
    def announceTopology(self):
        ngroups = len(self.groupList)
        groups = []
        for group in self.groupList:
            membership = []
            for node in group.nodes:
                membership.append(node.name)
            groups.append(membership)
                
        for group in self.groupList:
            for cust in group.customers:
                mesdict = {}
                mesdict["message_sender"] = self.name
                mesdict["message_target"] = cust.name
                mesdict["message_subject"] = "group_announcement"
                mesdict["your_group"] = group.name
                mesdict["group_membership"] = groups
                
                mess = json.dumps(mesdict)
                self.vip.pubsub.publish(peer = "pubsub", topic = "customerservice", headers = {}, message = mess)
    
    '''builds the connectivity matrix for the grid's infrastructure'''
    def rebuildConnMatrix(self):
        if settings.DEBUGGING_LEVEL >= 2:
            print("UTILITY {me} REBUILDING CONNECTIVITY MATRIX".format(me = self.name))
#        print("enumerate: " )
#        print(enumerate)
        for i,origin in enumerate(self.nodes):
#            print(origin.name)
            for edge in origin.originatingedges:
#                print("edge.name " + edge.name)
                for j, terminus in enumerate(self.nodes):
#                    print("terminus.name " + terminus.name)
                    if edge.endNode is terminus:
                        print("            terminus match! {i},{j}".format(i = i, j = j))
                        if edge.checkRelaysClosed():
                            self.connMatrix[i][j] = 1
                            self.connMatrix[j][i] = 1
                            print("                closed!")
                        else:
                            self.connMatrix[i][j] = 0
                            self.connMatrix[j][i] = 0                    
                            print("                open!")
        

                        
        if settings.DEBUGGING_LEVEL >= 2:
            print("UTILITY {me} HAS FINISHED REBUILDING CONNECTIVITY MATRIX".format(me = self.name))
            print("{mat}".format(mat = self.connMatrix))
        
    def marketfeed(self, peer, sender, bus, topic, headers, message):
        print("TEMP DEBUG - UTILITY: {mes}".format(mes = message))
        mesdict = json.loads(message)
        messageSubject = mesdict.get("message_subject",None)
        messageTarget = mesdict.get("message_target",None)
        messageSender = mesdict.get("message_sender",None)        
        
        if listparse.isRecipient(messageTarget,self.name):            
            if settings.DEBUGGING_LEVEL >= 2:
                print("\nUTILITY {me} RECEIVED AN ENERGYMARKET MESSAGE: {type}".format(me = self.name, type = messageSubject))
            if messageSubject == "bid_response":
                side = mesdict.get("side",None)
                print("side: {sid}".format(sid = side))
        
                rate =  mesdict.get("rate",None)
                amount = mesdict.get("amount",None)
                period = mesdict.get("period_number",None)
                uid = mesdict.get("uid",None)
                resourceName = mesdict.get("resource_name",None)
                
                #switch counterparty
                mesdict["counterparty"] = messageSender
                
                if side == "supply":
                    service = mesdict.get("service",None)
                    auxilliaryService = mesdict.get("auxilliary_service",None)
                    newbid = control.SupplyBid(**mesdict)
                    if service == "power":
                        self.supplyBidList.append(newbid)
                    elif service == "reserve":                  
                        self.reserveBidList.append(newbid)
                    #write to database
                    self.dbnewbid(newbid,self.dbconn,self.t0)
                elif side == "demand":
                    newbid = control.DemandBid(**mesdict)
                    self.demandBidList.append(newbid)
                    #write to database
                    self.dbnewbid(newbid,self.dbconn,self.t0)
                
                if settings.DEBUGGING_LEVEL >= 1:
                    print("UTILITY {me} RECEIVED A {side} BID#{id} FROM {them}".format(me = self.name, side = side,id = uid, them = messageSender ))
                    if settings.DEBUGGING_LEVEL >= 2:
                        newbid.printInfo(0)
            elif messageSubject == "bid_acceptance":
                pass
                #dbupdatebid()
            else:
                print("UTILITY {me} RECEIVED AN UNSUPPORTED MESSAGE TYPE: {msg} ON THE energymarket TOPIC".format(me = self.name, msg = messageSubject))
                
    '''callback for demandresponse topic'''
    def DRfeed(self, peer, sender, bus, topic, headers, message):
        mesdict = json.loads(message)
        messageSubject = mesdict.get("message_subject",None)
        messageTarget = mesdict.get("message_target",None)
        messageSender = mesdict.get("message_sender",None)
        if listparse.isRecipient(messageTarget,self.name):
            if messageSubject == "DR_enrollment":
                messageType = mesdict.get("message_type",None)
                if messageType == "enrollment_reply":
                    if mesdict.get("opt_in"):
                        custobject = listparse.lookUpByName(messageSender,self.customers)
                        self.DRparticipants.append(custobject)
                        
                        resdict = {}
                        resdict["message_target"] = messageSender
                        resdict["message_subject"] = "DR_enrollment"
                        resdict["message_type"] = "enrollment_confirm"
                        resdict["message_sender"] = self.name
                        
                        response = json.dumps(resdict)
                        self.vip.pubsub.publish("pubsub","demandresponse",{},response)
                        
                        if settings.DEBUGGING_LEVEL >= 1:
                            print("ENROLLMENT SUCCESSFUL! {me} enrolled {them} in DR scheme".format(me = self.name, them = messageSender))
    
    @Core.periodic(settings.RESOURCE_MEASUREMENT_INTERVAL)
    def resourceMeasurement(self):
        for res in self.Resources:
            self.dbupdateresource(res,self.dbconn,self.t0)
            
    def dbconsumption(self,cust,pow,dbconn,t0):
        command = 'INSERT INTO consumption (logtime, et, period, name, power) VALUES ("{time}",{et},{per},"{name}",{pow})'.format(time = datetime.utcnow().isoformat(), et = time.time() - t0, per = self.CurrentPeriod.periodNumber,name = cust.name, pow = pow)
        self.dbwrite(command,dbconn)
                
    def dbupdateresource(self,res,dbconn,t0):
        ch = res.DischargeChannel
        meas = tagClient.readTags([ch.unregVtag, ch.unregItag, ch.regVtag, ch.regItag],"source")
        command = 'INSERT INTO resstate (logtime, et, period, name, connected, reference_voltage, setpoint, inputV, inputI, outputV, outputI) VALUES ("{time}",{et},{per},"{name}",{conn},{refv},{setp},{inv},{ini},{outv},{outi})'.format(time = datetime.utcnow().isoformat(), et = time.time() - t0, per = self.CurrentPeriod.periodNumber, name = res.name, conn = int(res.connected), refv = ch.refVoltage, setp = ch.setpoint, inv = meas[ch.unregVtag], ini = meas[ch.unregItag] , outv = meas[ch.regVtag], outi = meas[ch.regItag])
        self.dbwrite(command,dbconn)
    
    def dbnewcustomer(self,newcust,dbconn,t0):
        cursor = dbconn.cursor()
        command = 'INSERT INTO customers VALUES ("{time}",{et},"{name}","{location}")'.format(time = datetime.utcnow().isoformat(), et = time.time() - t0, name = newcust.name, location = newcust.location)
        cursor.execute(command)
        dbconn.commit()
        cursor.close()
        
    def dbinfmeas(self,signal,value,dbconn,t0):
        command = 'INSERT INTO infmeas (logtime, et, period, signame, value) VALUES ("{time}",{et},{per},"{sig}",{val})'.format(time = datetime.utcnow().isoformat(), et = time.time() - t0,per = self.CurrentPeriod.periodNumber,sig = signal,val = value)
        self.dbwrite(command,dbconn)
        
    def dbtopo(self,topo,dbconn,t0):
        command = 'INSERT INTO topology (logtime, et, period, topology) VALUES("{time}",{et},{per},"{top}")'.format(time = datetime.utcnow().isoformat(), et = time.time() - t0,per = self.CurrentPeriod.periodNumber, top = topo)
        self.dbwrite(command,dbconn)
        
    def dbnewbid(self,newbid,dbconn,t0):
        if hasattr(newbid,"service"):
            if hasattr(newbid,"auxilliary_service"):
                command = 'INSERT INTO bids (logtime, et, period, id, side, service, aux_service, resource_name, counterparty_name, orig_rate, orig_amount) VALUES ("{time}",{et},{per},{id},"{side}","{serv}","{aux}","{resname}","{cntrname}",{rate},{amt})'.format(time = datetime.utcnow().isoformat(), et = time.time() - t0, per = newbid.periodNumber,id = newbid.uid, side = newbid.side, serv = newbid.service, aux = newbid.auxilliary_service, resname = newbid.resourceName, cntrname = newbid.counterparty, rate = newbid.rate, amt = newbid.amount) 
            else:
                command = 'INSERT INTO bids (logtime, et, period, id, side, service, resource_name, counterparty_name, orig_rate, orig_amount) VALUES ("{time}",{et},{per},{id},"{side}","{serv}","{resname}","{cntrname}",{rate},{amt})'.format(time = datetime.utcnow().isoformat(), et = time.time() - t0, per = newbid.periodNumber,id = newbid.uid, side = newbid.side, serv = newbid.service, resname = newbid.resourceName, cntrname = newbid.counterparty, rate = newbid.rate, amt = newbid.amount) 
        else:
            command = 'INSERT INTO bids (logtime, et, period, id, side, resource_name, counterparty_name, orig_rate, orig_amount) VALUES ("{time}",{et},{per},{id},"{side}","{resname}","{cntrname}",{rate},{amt})'.format(time = datetime.utcnow().isoformat(), et = time.time() - t0, per = newbid.periodNumber,id = newbid.uid, side = newbid.side, resname = newbid.resourceName, cntrname = newbid.counterparty, rate = newbid.rate, amt = newbid.amount) 
        self.dbwrite(command,dbconn)
        
    def dbupdatebid(self,bid,dbconn,t0):
        if bid.accepted:
            if hasattr(bid,"service"):
                command = 'UPDATE bids SET accepted="{acc}",acc_for="{accfor}",settle_rate={rate},settle_amount={amt} WHERE id={id}'.format(acc = int(bid.accepted), accfor = bid.service, rate = bid.rate, amt = bid.amount, id = bid.uid)
            else:
                command = 'UPDATE bids SET accepted="{acc}",settle_rate={rate},settle_amount={amt} WHERE id={id}'.format(acc = int(bid.accepted), rate = bid.rate, amt = bid.amount, id = bid.uid)
        else:
            command = 'UPDATE bids SET accepted={acc} WHERE id={id}'.format(acc = int(bid.accepted), id = bid.uid)
        self.dbwrite(command,dbconn)
        
    def dbtransaction(self,cust,amt,type,dbconn,t0):
        command = 'INSERT INTO transactions VALUES("{time}",{et},{per},"{name}","{type}",{amt},{bal})'.format(time = datetime.utcnow().isoformat(),et = time.time()-t0,per = self.CurrentPeriod.periodNumber,name = cust.name,type = type, amt = amt, bal = cust.customerAccount.accountBalance )
        self.dbwrite(command,dbconn)
       
    def dbnewresource(self, newres, dbconn, t0):
        command = 'INSERT INTO resources VALUES("{time}",{et},"{name}","{type}","{owner}","{loc}", {pow})'.format(time = datetime.utcnow().isoformat(), et = time.time()-t0, name = newres.name, type = newres.__class__.__name__,owner = newres.owner, loc = newres.location, pow = newres.maxDischargePower)
        self.dbwrite(command,dbconn)
         
    def dbnewefficiency(self,generation,consumption,losses,unaccounted,dbconn, t0):
        command = 'INSERT INTO efficiency VALUES("{time}",{et},{per},{gen},{con},{loss},{unacc})'.format(time = datetime.utcnow().isoformat(), et = time.time() - t0, per = self.CurrentPeriod.periodNumber, gen = generation, con = consumption, loss = losses, unacc = unaccounted)
        self.dbwrite(command,dbconn)
        
    def dbrelayfault(self,location,measurement,dbconn,t0):
        command = 'INSERT INTO relayfaults VALUES("{time}",{et},{per},{loc},{meas},{res})'.format(time = datetime.utcnow().isoformat(), et = time.time() - t0, per = self.CurrentPeriod.periodNumber, loc = location, meas = measurement, res = resistance)
        self.dbwrite(command,dbconn)
            
    def dbwrite(self,command,dbconn):
        try:
            cursor = dbconn.cursor()
            cursor.execute(command)
            dbconn.commit()
            cursor.close()
        except Exception as e:
            print("dbase error")
            print(command)
            print(e)

    '''prints information about the utility and its assets'''
    def printInfo(self,verbosity):
        print("\n************************************************************************")
        print("~~SUMMARY OF UTILITY KNOWLEDGE~~")
        print("UTILITY NAME: {name}".format(name = self.name))
        
        print("--LIST ALL {n} UTILITY OWNED RESOURCES------".format(n = len(self.Resources)))
        for res in self.Resources:
            res.printInfo(1)
        print("--END RESOURCES LIST------------------------")
        print("--LIST ALL {n} CUSTOMERS----------------".format(n=len(self.customers)))
        for cust in self.customers:
            print("---ACCOUNT BALANCE FOR {them}: {bal} Credits".format(them = cust.name, bal = cust.customerAccount.accountBalance))
            cust.printInfo(1)
        print("--END CUSTOMER LIST---------------------")
        if verbosity > 1:
            print("--LIST ALL {n} DR PARTICIPANTS----------".format(n = len(self.DRparticipants)))
            for part in self.DRparticipants:
                part.printInfo(1)
            print("--END DR PARTICIPANTS LIST--------------")
            print("--LIST ALL {n} GROUPS------------------".format(n = len(self.groupList)))
            for group in self.groupList:
                group.printInfo(1)
            print("--END GROUPS LIST----------------------")
        print("~~~END UTILITY SUMMARY~~~~~~")
        print("*************************************************************************")


    '''get tag value by name, but use the tag client only if the locally cached
    value is too old, as defined in seconds by threshold'''
    def getLocalPreferred(self,tags,threshold, plc):
        reqlist = []
        outdict = {} 
        indict = {}
        
        for tag in tags:
            try:
                #check cache to see if the tag is fresher than the threshold
                val, time = self.tagCache.get(tag,[None,None])
                #how much time has passed since the tag was last read from the server?
                diff = datetime.now()-time
                #convert to seconds
                et = diff.total_seconds()                
            except Exception:
                val = None
                et = threshold
                
            #if it's too old, add it to the list to be requested from the server    
            if et > threshold or val is None:
                reqlist.append(tag)
            #otherwise, add it to the output
            else:
                outdict[tag] = val
                
        #if there are any tags to be read from the server get them all at once
        if reqlist:
            indict = tagClient.readTags(reqlist,plc)
            if len(indict) == 1:
                outdict[reqlist[0]] = indict[reqlist[0]]
            else:
                for updtag in indict:
                    #then update the cache
                    self.tagCache[updtag] = (indict[updtag], datetime.now())
                    #then add to the output
                    outdict[updtag] = indict[updtag]
            
            #output should be an atom if possible (i.e. the request was for 1 tag
            if len(outdict) == 1:
                return outdict[tag]
            else:
                return outdict
    
    '''get tag by name from tag server'''
    def getTag(self,tag, plc):
         return tagClient.readTags([tag],plc)[tag]
        
    '''open an infrastructure relay. note that the logic is backward. this is
    because I used the NC connection of the SPDT relays for these'''
    def openInfRelay(self,rname):
        tagClient.writeTags([rname],[True],"load")
        
class BidState(object):
    def __init__(self):
        self.reservepolicy = False
        self.supplypolicy = False
        self.demandpolicy = False
        
        self.ignorelist = []
        
    def acceptall(self):
        self.reservepolicy = True
        self.supplypolicy = True
        self.demandpolicy = True
        
    def reserveonly(self):
        self.reservepolicy = True
        self.supplypolicy = False
        self.demandpolicy = False
        
    def acceptnone(self):
        self.reservepolicy = False
        self.supplypolicy = False
        self.demandpolicy = False
        
    def addtoignore(self,name):
        self.ignorelist.append(name)
    



        
def main(argv = sys.argv):
    try:
        utils.vip_main(UtilityAgent)
    except Exception as e:
        _log.exception('unhandled exception')
        
if __name__ == '__main__':
    sys.exit(main())



