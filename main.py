from typing import ParamSpec
import requests
import sys
import getopt
import json
from azure.identity import ClientSecretCredential
from azure.mgmt.network import NetworkManagementClient
import time

def resize_gateway(controllerIp, controllerUser, controllerPassword, gatewayName, newGwSize, azureTenantId, azureClientId, azureSubscriptionId, azureClientSecret):
    #Get a session token
    url = "https://" + controllerIp + "/v1/api"
    payload={ 'action': 'login','username': controllerUser,'password': controllerPassword}
    response = requests.request("POST", url, headers={}, data=payload)

    cid = json.loads(response.text)["CID"]

    #Check if new gateway size is supported
    payload={ 'action': 'get_gateway_supported_size','CID': cid}
    response = requests.request("GET", url, headers={}, params=payload)
    jsonResponse = json.loads(response.text)

    if jsonResponse["return"] == False:
        print ("The following error occured: " + jsonResponse["reason"])
        return

    if newGwSize not in jsonResponse["results"]["8"]:
        print ("The selected gateway size is unsupported. Select a supported gateway size.")
        return

    #Get current gateway size (HA gateway)
    payload={ 'action': 'get_gateway_info','CID': cid,'gateway_name': gatewayName + "-hagw"}
    response = requests.request("GET", url, headers={}, params=payload)
    jsonResponse = json.loads(response.text)

    if jsonResponse["return"] == False:
        print ("The following error occured: " + jsonResponse["reason"])
        return

    print("Current gateway size: " + jsonResponse["results"]["vpc_size"])

    if newGwSize == jsonResponse["results"]["vpc_size"]:
        print ("Current and new gateway size are the same. Nothing to do.")
        return

    if jsonResponse["results"]["vendor_name"] != "Azure ARM":
        print ("Only Azure gateways supported. Exiting.")
        return
    
    if jsonResponse["results"]["spoke_vpc"] != "yes":
        print("Only Spoke gateways supported. Exiting.")
        return
    
    #Get HA GW private Ip address
    haGwPrivateIp = jsonResponse["results"]["private_ip"] 
    print ("HA Gateway private IP: " + haGwPrivateIp)

    #Get the main gateway private IP
    payload={ 'action': 'get_gateway_info','CID': cid,'gateway_name': gatewayName}
    response = requests.request("GET", url, headers={}, params=payload)
    jsonResponse = json.loads(response.text)

    mainGwPrivateIp = jsonResponse["results"]["private_ip"] 
    print ("Main Gateway private IP: " + mainGwPrivateIp)

    #If need to resize, identify the routing tables in the VNET of the gateway
    payload={ 'action': 'list_vpc_route_tables','CID': cid,'account_name': jsonResponse["results"]["account_name"], 'vpc_region': jsonResponse["results"]["vpc_region"], 'vpc_id': jsonResponse["results"]["vpc_id"]}
    response = requests.request("GET", url, headers={}, params=payload)
    jsonResponse = json.loads(response.text)

    if jsonResponse["return"] == False:
        print ("The following error occured while querying route tables: " + jsonResponse["reason"])
        return

    routeTables = jsonResponse["results"]["vpc_rtbs_list"]

    #Log in to Azure
    credentials = ClientSecretCredential(
            client_id=azureClientId,
            client_secret=azureClientSecret,
            tenant_id=azureTenantId
        )

    try:
        network_client = NetworkManagementClient(credentials, azureSubscriptionId)

        #Variable to save current routing table state into
        originalRoutes = {}
    
        for routeTable in routeTables:
            originalRoutes[routeTable.split(":")[0]] = []
            for route in network_client.routes.list(routeTable.split(":")[1], routeTable.split(":")[0]):
                routeDict = {}
                routeDict["name"] = route.name
                routeDict["id"] = route.id
                routeDict["rg_name"] = routeTable.split(":")[1]
                routeDict["prefix"] = route.address_prefix
                routeDict["nh_type"] = route.next_hop_type
                routeDict["next_hop"] = route.next_hop_ip_address

                originalRoutes[routeTable.split(":")[0]].append(routeDict)
    
    except:
        print("Azure login unsuccessful. Check credentials.")
        return


    #Save the original routes into a file
    saveFile = open("routes_save.txt", "w")
    saveFile.write(json.dumps(originalRoutes))
    saveFile.close()

    #Update the routes pointing to the HA gw to point to main gateway
    for routeTable in routeTables:
        for route in network_client.routes.list(routeTable.split(":")[1], routeTable.split(":")[0]):
            if route.next_hop_ip_address == haGwPrivateIp:
                route.next_hop_ip_address = mainGwPrivateIp
                network_client.routes.begin_create_or_update(routeTable.split(":")[1], routeTable.split(":")[0], route.name, route)
    
    #Need to wait for Azure to do its thing in updating routes
    print ("Waiting for routes to be updated.")
    time.sleep(30)

    #Resize HA GW
    print ("Resizing HA gateway")
    payload={ 'action': 'change_gateway_size','CID': cid,'gw_name': gatewayName + "-hagw", 'gw_size': newGwSize}
    response = requests.request("POST", url, headers={}, data=payload)
    jsonResponse = json.loads(response.text)
    print ("HA gateway resized")

    #Identify the routes pointing to main gateway, update to HA gateway
    for routeTable in routeTables:
        for route in network_client.routes.list(routeTable.split(":")[1], routeTable.split(":")[0]):
            if route.next_hop_ip_address == mainGwPrivateIp:
                route.next_hop_ip_address = haGwPrivateIp
                network_client.routes.begin_create_or_update(routeTable.split(":")[1], routeTable.split(":")[0], route.name, route)
    
    #Need to wait for Azure to do its thing in updating routes
    print ("Waiting for routes to be updated.")
    time.sleep(120)

    #Resize main gw
    print ("Resizing main gateway")
    payload={ 'action': 'change_gateway_size','CID': cid,'gw_name': gatewayName, 'gw_size': newGwSize}
    response = requests.request("POST", url, headers={}, data=payload)
    jsonResponse = json.loads(response.text)
    print ("Main gateway resized")

    #Restore original route table state
    print ("Restoring original route tables")

    for routeTable in routeTables:
        for route in network_client.routes.list(routeTable.split(":")[1], routeTable.split(":")[0]):
            originalRoute = {}

            for originalRouteEntry in originalRoutes[routeTable.split(":")[0]]:
                if route.id == originalRouteEntry["id"]:
                    originalRoute = originalRouteEntry

            if route.next_hop_ip_address != originalRoute["next_hop"]:
                route.next_hop_ip_address = originalRoute["next_hop"]
                network_client.routes.begin_create_or_update(routeTable.split(":")[1], routeTable.split(":")[0], route.name, route)

    print ("Waiting for routes to be updated.")
    time.sleep(120)

    print ("Original route tables restored")

    return

def print_help():
    print ('''
Usage: main.py [options]

Resize Aviatrix Spoke Gateway

Options:
  -h, --help            Show this help message and exit
  -c <controller_ip>, --controller_ip <controller_ip>
                        Required argument: Aviatrix controller IP or hostname
  -u <user>, --controller_user=<user>
                        Required argument: Aviatrix controller username
  -p <password>, --controller_password=<password>
                        Required argument: Aviatrix controller password
  -g <gateway_name>, --gateway_name=<gateway_name>
                        Required argument: Aviatrix spoke gateway name (main gateway)
  -s <gateway_size>, --gateway_size=<new_size>
                        Required argument: AThe new gateway size
''')

def main(argv=None):
    '''
    Main function: work with command line options and send an HTTPS request to the Aviatrix Controller API.
    '''

    try:
        opts, args = getopt.getopt(sys.argv[1:], 'hc:u:p:g:s:',
                                   ['help', 'controller_ip=', 'controller_user=', 'controller_password=', 'gateway_name=', 'gateway_size='])
    except (getopt.GetoptError):
        # Print help information and exit:
        print_help()
        sys.exit(2)

    # Initialize parameters
    controllerIp = None
    controllerUser = None
    controllerPassword = None
    gatewayName = None
    gatewaySize = None

    # Parse command line options
    for opt, arg in opts:
        if opt in ('-h', '--help'):
            print_help()
            sys.exit()
        elif opt in ('-c', '--controller_ip'):
            controllerIp = arg
        elif opt in ('-u', '--controller_user'):
            controllerUser = arg
        elif opt in ('-p', '--controller_password'):
            controllerPassword = arg
        elif opt in ('-g', '--gateway_name'):
            gatewayName = arg
        elif opt in ('-s', '--gateway_size'):
            gatewaySize = arg

    # Enforce required arguments
    if not controllerIp or not controllerUser or not controllerPassword or not gatewayName or not gatewaySize:
      print_help()
      sys.exit(4)

    #Ask for Azure credentials
    azureTenantId = ""
    azureClientId = ""
    azureClientSecret = ""
    azureSubscriptionId = ""

    if azureTenantId == "":
        azureTenantId = input("Enter Azure Tenant Id:")
    if azureClientId == "":
        azureClientId = input("Enter Azure Client Id:")
    if azureSubscriptionId == "":
        azureSubscriptionId = input("Enter Azure Subscription Id:")
    if azureClientSecret == "":
        azureClientSecret = input("Enter Azure Client Secret:")

    #Resize gateway
    resize_gateway(controllerIp, controllerUser, controllerPassword, gatewayName, gatewaySize, azureTenantId, azureClientId, azureSubscriptionId, azureClientSecret)



if __name__ == '__main__':
    sys.exit(main())
