import boto3
import hashlib
import json
import logging
import os
import time

logger = logging.getLogger()
logger.setLevel(logging.DEBUG)

aga_client = boto3.client('globalaccelerator', region_name='us-west-2')

# Accelerator Parameters
ENDPOINT_GROUP_ARN = <Endpoint_ARN> # String | Endpoint ARN (not the Accelerator ARN), make sure it is configured for endpoints in this region; it should look like 'arn:aws:globalaccelerator::123456789012:accelerator/c9d8f18d-e6a7-4f28-ae95-261507146530/listener/461df876/endpoint-group/c3770cbbf005'
ENDPOINT_WEIGHT = 128 # Number, default is 128 | Applies only to the new EC2 endpoint.
CLIENT_IP_PRESERVATION = True # True or False, default is True | Applies only to the new EC2 endpoint.

DETAIL_TYPE = "detail-type"
LIFECYCLE_HOOK_NAME = "LifecycleHookName"
EC2_ID = "EC2InstanceId"
ASG_GROUP = "AutoScalingGroupName"

def check_response(response_json):
    try:
        if response_json['ResponseMetadata']['HTTPStatusCode'] == 200:
            return True
        else:
            return False
    except KeyError:
        return False

def list_endpoints(): # List all the endpoints associated to the endpoint group - Important because the endpoint group may have other endpoints that are not member of the autoscaling group.
    response = aga_client.describe_endpoint_group(
        EndpointGroupArn = ENDPOINT_GROUP_ARN
    )
    return response

def abandon_lifecycle(life_cycle_hook, auto_scaling_group, instance_id):
    asg_client = boto3.client('autoscaling')
    try:
        response = asg_client.complete_lifecycle_action(
            LifecycleHookName = life_cycle_hook,
            AutoScalingGroupName = auto_scaling_group,
            LifecycleActionResult = 'ABANDON',
            InstanceId = instance_id
            )
        if check_response(response):
            logger.info("Lifecycle hook abandoned correctly: %s", response)
        else:
            logger.error("Lifecycle hook could not be abandoned: %s", response)
    except Exception as e:
        logger.error("Lifecycle hook abandon could not be executed: %s", str(e))
        return None

def updated_endpoints_list(detail_type, instance_id):
    endpoints = []
    response = list_endpoints()

    if detail_type == 'EC2 Instance Launch Successful': # Add the endpoint to the Accelerator
        for EndpointID in response['EndpointGroup']['EndpointDescriptions']:
            result = {'EndpointId': EndpointID['EndpointId'],'Weight': EndpointID['Weight']}
            endpoints.append(result)

        # Endpoint to add
        endpoints.append({'EndpointId': instance_id,'Weight': ENDPOINT_WEIGHT,'ClientIPPreservationEnabled': CLIENT_IP_PRESERVATION})

    elif detail_type == 'EC2 Instance-terminate Lifecycle Action': # Remove the endpoint from the Accelerator
        for EndpointID in response['EndpointGroup']['EndpointDescriptions']:
            if EndpointID['EndpointId'] != instance_id: # Remove the endpoint from the list of endpoints
                result = {'EndpointId': EndpointID['EndpointId'],'Weight': EndpointID['Weight']}
                endpoints.append(result)
    return endpoints

def update_endpoint_group(detail_type, instance_id):
    try:
        response = aga_client.update_endpoint_group(
            EndpointGroupArn = ENDPOINT_GROUP_ARN,
            EndpointConfigurations = updated_endpoints_list(detail_type, instance_id)
            )
        if check_response(response):
            logger.info("The endpoint group has been updated: %s", response)
            return response['EndpointGroup']['EndpointDescriptions']
        else:
            logger.error("Could not update the endpoint group: %s", response)
            return None
    except Exception as e:
        logger.error("Could not update the endpoint group: %s", str(e))
        return None

def lambda_handler(event, context):
    try:
        logger.info(json.dumps(event))
        message = event['detail']
        detail_type = event[DETAIL_TYPE]
        if ASG_GROUP in message:
            instance_id = message[EC2_ID]
            response = update_endpoint_group(detail_type, instance_id)
            if response != None:
                logging.info("Lambda executed correctly")
            elif detail_type == 'EC2 Instance-terminate Lifecycle Action':
                auto_scaling_group = message[ASG_GROUP]
                life_cycle_hook = message[LIFECYCLE_HOOK_NAME]
                abandon_lifecycle(life_cycle_hook, auto_scaling_group, instance_id)
        else:
            logging.error("No valid JSON message: %s", parsed_message)
    except Exception as e:
        logging.error("Error: %s", str(e))
