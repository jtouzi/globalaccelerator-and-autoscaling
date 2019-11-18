'''
Copyright 2018 Amazon.com, Inc. or its affiliates. All Rights Reserved.
Licensed under the Apache License, Version 2.0 (the "License"). You may not use this file except in compliance with the License. A copy of the License is located at
    http://aws.amazon.com/apache2.0/
or in the "license" file accompanying this file. This file is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the License for the specific language governing permissions and limitations under the License.
'''


import boto3
import hashlib
import json
import logging
import urllib.request, urllib.error, urllib.parse

logger = logging.getLogger()
logger.setLevel(logging.DEBUG)

aga_client = boto3.client('globalaccelerator')

# Accelerator Parameters
ENDPOINT_GROUP_ARN = "arn:aws:globalaccelerator::071855492661:accelerator/c9d8f18d-e6a7-4f28-ae95-261507146530/listener/461df876/endpoint-group/c3770cbbf005"
ENDPOINT_WEIGHT = 128
CLIENT_IP_PRESERVATION = True

def lambda_handler(event, context):
    # print(("Received event: " + json.dumps(event, indent=2)))
    Lifecycle_Hook_Name = event['details']['LifecycleHookName'] # "ASG-AGA-Launching" OR "ASG-AGA-Terminaning",
    ec2_instance = event['details']['EC2InstanceId']
    # return detail_type
    
    response = aga_client.describe_endpoint_group(
        EndpointGroupArn = ENDPOINT_GROUP_ARN
    )

    endpoints = []
    
    if Lifecycle_Hook_Name == 'ASG-AGA-Launching': # Add the endpoint to the Accelerator
        for EndpointID in response['EndpointGroup']['EndpointDescriptions']:
            result = {'EndpointId': EndpointID['EndpointId'],'Weight': EndpointID['Weight'],'ClientIPPreservationEnabled': CLIENT_IP_PRESERVATION}
            endpoints.append(result)
        # Endpoint to add
        endpoints.append({'EndpointId': ec2_instance,'Weight': ENDPOINT_WEIGHT,'ClientIPPreservationEnabled': CLIENT_IP_PRESERVATION})
    elif Lifecycle_Hook_Name == 'ASG-AGA-Terminaning': # Remove the endpoint from the Accelerator
        for EndpointID in response['EndpointGroup']['EndpointDescriptions']:
            if EndpointID['EndpointId'] != ec2_instance:
                result = {'EndpointId': EndpointID['EndpointId'],'Weight': EndpointID['Weight'],'ClientIPPreservationEnabled': CLIENT_IP_PRESERVATION}
        endpoints.append(result)
    

    response = aga_client.update_endpoint_group(
        EndpointGroupArn = ENDPOINT_GROUP_ARN,
        EndpointConfigurations = endpoints
    )
