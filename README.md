## Using AWS Lambda to automatically update AWS Global Accelerator EC2 endpoints based on AWS Autoscaling Groups events

## Introduction
[AWS Global Accelerator](https://aws.amazon.com/global-accelerator/) is a service that improves the availability and performance of your applications with local or global users. It provides static IP addresses that act as a fixed entry point to your application endpoints in a single or multiple AWS Regions, such as your Application Load Balancers, Network Load Balancers or Amazon EC2 instances. To front an EC2 instance with Global Accelerator, you simply create an accelerator and add the EC2 instance as an endpoint using the EC2 instance ID. To control what internet traffic reaches your EC2 instance, you can use security groups in your VPC. Additionally, Global Accelerator preserves the source IP address of the client all the way to the EC2 instance, which enables you to apply client-specific logic and serve personalised content for your TCP and UDP applications.

Applications running on [Amazon EC2 instances can be directly fronted by AWS Global Accelerator](https://aws.amazon.com/about-aws/whats-new/2019/10/aws-global-accelerator-supports-ec2-instance-endpoints/). Some customers use AWS Auto Scaling service to automatically adjusts capacity to maintain steady, predictable performance at the lowest possible cost. The EC2 instances in an Auto Scaling group have a lifecycle that differs from that of other EC2 instances, it starts when the Auto Scaling group launches an instance and puts it into service, and ends when you terminate the instance, or the Auto Scaling group takes the instance out of service and terminates it. Currently AWS Global Accelerator does not support out of the box adding/removing EC2 endpoints to/from an endpoint group based on Autoscaling events. In this blog post I will show you how to use AWS Lambda to automatically add EC2 endpoints to an endpoint group, or remove EC2 endpoints from an endpoint group based on Autoscaling group events. We recommend that you remove an EC2 instance from Global Accelerator endpoint groups before you terminate the instance, we will leverage [Auto Scaling lifecycle hooks](https://docs.aws.amazon.com/autoscaling/ec2/userguide/lifecycle-hooks.html) to remove an instance selected for termination from the endpoint group before it is terminated.

## Prerequisites and Caveats
Make sure you have the following completed:

- You have AWS CLI installed and configured, using credentials with sufficient permissions.
- Your Autoscaling group is created and configured (note down it name).
- Your Accelerator and the endpoint group are created (note down it endpoint group ARN)

## Step 1 - Create and configure the Lambda Function’s IAM role

Lambda functions need an IAM role to give them their execution permissions. To create the IAM role, we’ll need two policies:

- the first is a trust policy (assumed role) allowing the Lambda service to assume the role,
- the second (inline policy) gives the role permission to publish to:
  - complete the autoscaling lifecycle action,
  - update the accelerator endpoint,
  - push logs to CloudWatch.

### Trust policy
Create a text file called Lambda-Role-Trust-Policy.json with the following content:
```
{
	"Version": "2012-10-17",
	"Statement": [{
		"Sid": "",
		"Effect": "Allow",
		"Principal": {
			"Service": "lambda.amazonaws.com"
		},
		"Action": "sts:AssumeRole"
	}]
}
```

Create a policy with this trust policy:
```
$ aws iam create-role \
--role-name ASG_AGA-Lambda-Role \
--assume-role-policy-document file://Lambda-Role-Trust-Policy.json
```

### Inline policy
Create a text file called Lambda-Role-Inline-Policy.json with the following content:
```
{
	"Version": "2012-10-17",
	"Statement": [{
			"Effect": "Allow",
			"Action": [
				"autoscaling:CompleteLifecycleAction"
			],
			"Resource": "*"
		},
		{
			"Effect": "Allow",
			"Action": [
				"logs:CreateLogGroup",
				"logs:CreateLogStream",
				"logs:PutLogEvents"
			],
			"Resource": "arn:aws:logs:*:*:*"
		},
		{
			"Effect": "Allow",
			"Action": [
				"globalaccelerator:UpdateEndpointGroup",
				"globalaccelerator:DescribeEndpointGroup"
			],
			"Resource": "*"
		}
	]
}
```
Note: For the Global Accelerator actions, you can update the Resource from "\*\" to the endpoint ARN.

Attach the inline policy to the IAM role we just created:
```
$ aws iam put-role-policy \
--role-name ASG_AGA-Lambda-Role \
--policy-name AutoScalingGlobalAcceleratorWithLogging \
--policy-document file://Lambda-Role-Inline-Policy.json
Step 2 - Put the lifecycle hook for instance terminating
$ aws autoscaling put-lifecycle-hook \
--lifecycle-hook-name ASG-AGA-Terminating \
--auto-scaling-group-name My-ASG-Group-Name \
--lifecycle-transition autoscaling:EC2_INSTANCE_TERMINATING \
--heartbeat-timeout 60
```

## Step 3 - Create the Lambda function
The Lambda function uses modules included in the Python 3.7 Standard Library and the AWS SDK for Python module (boto3), which is preinstalled as part of Lambda. The function code performs the following:

- Gets the EC2 instance ID that is being launched or terminated from the CloudWatch Event,
- Lists the endpoints attached to the endpoint group (describeEndpointGroup API),
- Updates the endpoint group (updateEndpointGroup API) by removing the endpoint from the endpoint group if the instance is being terminated OR by adding the endpoint to the endpoint group if the instance is being launched.
- Checks the status of updateEndpointGroup API call and if it fails, completes the lifecycle hook.

Create a text file called asg_aga_function.py with the following content:
```
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
```
Zip the file and use the CLI command below to create the lambda function with the name ASG_AGA-Function. The function will use the IAM role we created in step 1, and set the timeout for 30 seconds.
```
$ zip asg_aga_function.zip asg_aga_function.py
$ aws lambda create-function \
--function-name ASG_AGA-Function \
--runtime Python 3.7 \
--zip-file fileb://asg_aga_function.zip \
--role arn:aws:iam::123456789012:role/ASG_AGA-Lambda-Role \
--handler asg_aga_function.handler \
--timeout 30
```

## Step 4 - Configure CloudWatch Events to trigger the Lambda function
The Lambda function will be triggered every time the Autoscaling group launches an EC2 instance (*"EC2 Instance Launch Successful"* event) or terminates an EC2 instance, for this we need to make sure the EC2 endpoint is removed from the accelerator endpoint before it is terminated, we will use the lifecycle hook for this (*"EC2 Instance-terminate Lifecycle Action"* event).

Create a text file called eventPattern.json with the following content:
```
{
	"source": ["aws.autoscaling"],
	"detail-type": ["EC2 Instance-launch Lifecycle Action", "EC2 Instance-terminate Lifecycle Action"],
	"detail": {
		"AutoScalingGroupName": ["MY-ASG-Group-Name"]
	}
}
```
Use the event pattern to create the rule as follows:
```
$ aws events put-rule \
--name ASG-AGA-Rule \
--event-pattern file://eventPattern.json
Add the Lambda function as Target for the Rule:

$ aws events put-targets
--rule ASG-AGA-Rule \
--targets "Id"="1","Arn"="arn:aws:lambda:us-west-2:123456789012:function:ASG_AGA-Function"
```
