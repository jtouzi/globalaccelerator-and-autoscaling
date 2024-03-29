## Using AWS Lambda to automatically update AWS Global Accelerator EC2 endpoints based on AWS Autoscaling Groups events

## Introduction
[AWS Global Accelerator](https://aws.amazon.com/global-accelerator/) is a service that improves the availability and performance of your applications with local or global users. It provides static IP addresses that act as a fixed entry point to your application endpoints in a single or multiple AWS Regions, such as your Application Load Balancers, Network Load Balancers or Amazon EC2 instances. To front an EC2 instance with Global Accelerator, you simply create an accelerator and add the EC2 instance as an endpoint using the EC2 instance ID. To control what internet traffic reaches your EC2 instance, you can use security groups in your VPC. Additionally, Global Accelerator preserves the source IP address of the client all the way to the EC2 instance, which enables you to apply client-specific logic and serve personalised content for your TCP and UDP applications.

Applications running on [Amazon EC2 instances can be directly fronted by AWS Global Accelerator](https://aws.amazon.com/about-aws/whats-new/2019/10/aws-global-accelerator-supports-ec2-instance-endpoints/). Some customers use AWS Auto Scaling service to automatically adjusts capacity to maintain steady, predictable performance at the lowest possible cost. The EC2 instances in an Auto Scaling group have a lifecycle that differs from that of other EC2 instances, it starts when the Auto Scaling group launches an instance and puts it into service, and ends when you terminate the instance, or the Auto Scaling group takes the instance out of service and terminates it. Currently AWS Global Accelerator does not support out of the box adding/removing EC2 endpoints to/from an endpoint group based on Autoscaling events. In this blog post I will show you how to use AWS Lambda to automatically add EC2 endpoints to an endpoint group, or remove EC2 endpoints from an endpoint group based on Autoscaling group events. We recommend that you remove an EC2 instance from Global Accelerator endpoint groups before you terminate the instance, we will leverage [Auto Scaling lifecycle hooks](https://docs.aws.amazon.com/autoscaling/ec2/userguide/lifecycle-hooks.html) to remove an instance selected for termination from the endpoint group before it is terminated.

## Solution Overview
![Using AWS Lambda to automatically update AWS Global Accelerator EC2 endpoints based on AWS Autoscaling Groups events](https://jtouzi.s3.amazonaws.com/autoscaling_lambda_globalaccelerator.png)

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
> Note: For the Global Accelerator actions, you can update the Resource from "\*\" to the endpoint ARN.

Attach the inline policy to the IAM role we just created:
```
$ aws iam put-role-policy \
	--role-name ASG_AGA-Lambda-Role \
	--policy-name AutoScalingGlobalAcceleratorWithLogging \
	--policy-document file://Lambda-Role-Inline-Policy.json
```
## Step 2 - Put the lifecycle hook for instance terminating
```
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

Download [asg_aga_function.py](asg_aga_function.py), zip it and use the CLI command below to create the lambda function with the name ASG_AGA-Function. The function will use the IAM role we created in step 1, and set the timeout for 30 seconds.
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
		"AutoScalingGroupName": ["<MY-ASG-Group-Name>"]
	}
}
```
Use the event pattern to create the rule as follows:
```
$ aws events put-rule \
	--name ASG-AGA-Rule \
	--event-pattern file://eventPattern.json
```
Add the Lambda function as Target for the Rule:
```
$ aws events put-targets
	--rule ASG-AGA-Rule \
	--targets "Id"="1","Arn"="arn:aws:lambda:us-west-2:123456789012:function:ASG_AGA-Function"
```
## Step 5 - Test the environment
From the [Auto Scaling console](https://console.aws.amazon.com/ec2/autoscaling), you can change the desired capacity and the minimum for your Auto Scaling group to 0 so that the instance running starts being terminated. If any of these instances were endpoints for the accelerator endpoint group, you will notice that before they are terminated, they will be removed from the endpoint group. You can then change the Autoscaling group desired capacity and the minimum to the one expected, you will notice that the EC2 instances are added to the endpoint group as soon as they are successfully launched.

To test this with the CLI:
Update the Autoscaling group minimum and desired capacity:
```
$ aws autoscaling update-auto-scaling-group \
	--auto-scaling-group-name <MY-ASG-Group-Name> \
	--min-size 0 --desired-capacity 0
```
Describe the endpoint group:
```
$ aws globalaccelerator describe-endpoint-group \
	--endpoint-group-arn <MyEndpointGroupARN> \
	--region us-west-2
```
Increase the minimum and desired capacity and describe the endpoint group to see the changes.

If it does not work as expected, review the [CloudWatch logs](https://console.aws.amazon.com/cloudwatch/home?#logs:) to see the Lambda output. In the CloudWatch console, choose Logs and /aws/lambda/ASG_AGA-Function to see the execution output.
