# aviatrix-azure-gatewayresize
Code to resize Aviatrix Spoke Gateways with minimal downtime in Azure 

# What does this script do?
The script resizes an Aviatrix Spoke gateway in Azure in an efficient way to reduce traffic outage. During a normal gateway resize the VNET route tables are not updated, thus the resize may result in 120-140s of traffic outage. This script reduces this time by first pointing all UDRs to the primary gateway and resizing the HA gateway, then paointing all UDRs to the HA gateway and resizing the primary gateway. This reduces traffic outage to around two times 5-10s. This outage is caused by UDR updates. As UDRs are updated, some traffic is lost.

# Usage

Use the following paramateres when executing the script:
* -c Aviatrix controller IP
* -u Aviatrix controller username
* -p Aviatrix controller password
* -g Spoke gateway name to resize
* -s New gateway size
