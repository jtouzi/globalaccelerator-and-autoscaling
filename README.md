## Using AWS Lambda to automatically update Global Accelerator EC2 endpoints launched or terminated by Auto Scaling

## Introduction
[AWS Global Accelerator](https://aws.amazon.com/global-accelerator/) is a service that improves the availability and performance of your applications with local or global users. It provides static IP addresses that act as a fixed entry point to your application endpoints in a single or multiple AWS Regions, such as your Application Load Balancers, Network Load Balancers or Amazon EC2 instances. To front an EC2 instance with Global Accelerator, you simply create an accelerator and add the EC2 instance as an endpoint using the EC2 instance ID. To control what internet traffic reaches your EC2 instance, you can use security groups in your VPC. Additionally, Global Accelerator preserves the source IP address of the client all the way to the EC2 instance, which enables you to apply client-specific logic and serve personalized content for your TCP and UDP applications.

Some customers use AWS Auto Scaling service to automatically adjusts capacity to maintain steady, predictable performance at the lowest possible cost. The EC2 instances in an Auto Scaling group have a lifecycle that differs from that of other EC2 instances, it starts when the Auto Scaling group launches an instance and puts it into service, and ends when you terminate the instance, or the Auto Scaling group takes the instance out of service and terminates it. Currently AWS Global Accelerator does not support adding or removing EC2 endpoints to or from an endpoint group based on Auto Scaling events. In this blog post I will show you how to use AWS Lambda to automatically add EC2 endpoints to an endpoint group, or remove EC2 endpoints from an endpoint group based on Auto Scaling events. We recommend that you remove an EC2 instance from Global Accelerator endpoint groups before you terminate the instance, we will leverage [Auto Scaling lifecycle hooks](https://docs.aws.amazon.com/autoscaling/ec2/userguide/lifecycle-hooks.html) to remove an instance selected for termination from the endpoint group before it is terminated.

## Prerequistites and Caveats
Make sure you have the following completed:

- You have AWS CLI installed and configured, and are using credentials with sufficient permissions.
- Your Auto Scaling group is created and configured (note that the hook will only apply to new instances joining the pool).
- Your accelerator is created.

## Step 1 – Create an SNS topic and configure an IAM role to allow the Auto Scaling service to post to the SNS topic

### Create the SNS topic

```
aws sns create-topic --name AutoScaling-GlobalAccelerator-Topic
```
In the response, Note the ARN of the topic for later use.
```
{
    "TopicArn": "arn:aws:sns:us-west-2:012345678901:AutoScaling-GlobalAccelerator-Topic"
}
```
### Create and configure the IAM role

The lifecycle hook uses an IAM role to send messages to SNS, we’ll need two policies to create the IAM role:
- a trust (assumed role) policy allowing the Auto Scaling service to assume the role,
- a role permission (inline policy) to publish to the SNS topic.

#### Trust policy

1. Create a text file called SNS-Role-Trust-Policy.json with the following content

```
{
  "Version": "2012-10-17",
  "Statement": [ {
      "Sid": "",
      "Effect": "Allow",
      "Principal": {
        "Service": "autoscaling.amazonaws.com"
      },
      "Action": "sts:AssumeRole"
  } ]
}
```

2. Create a policy with this trust policy
```
$ aws iam create-role \
--role-name AutoScaling-GlobalAccelerator-Topic-Publisher-Role \
--assume-role-policy-document file://SNS-Role-Trust-Policy.json
```

#### Inline policy

1. Create a text file called SNS-Role-Inline-Policy.json with the following content

```
{
  "Version": "2012-10-17",
  "Statement": [ {
      "Effect": "Allow",
      "Resource": "arn:aws:sns:us-west-2:012345678901:AutoScaling-GlobalAccelerator-Topic",
      "Action": [
        "sns:Publish"
      ]
  } ]
}
```

2. Apply the inline policy to the IAM role we just created
```
$ aws iam put-role-policy \
--role-name AutoScaling-GlobalAccelerator-Topic-Publisher-Role \
--policy-name AllowPublishToASGAGATopic \
--policy-document file://SNS-Role-Inline-Policy.json
```

## Step 2 - Configure the Lambda Function’s IAM role

Like for the IAM role above we’ll need two policies:
- a trust (assumed role) policy allowing the Lambda service to assume the role,
- a role permission (inline policy) that defines what the Lambda function is allowed to do.

#### Trust policy

1. Create a text file called Lambda-Role-Trust-Policy.json with the following content

```
{
  "Version": "2012-10-17",
  "Statement": [ {
      "Sid": "",
      "Effect": "Allow",
      "Principal": {
        "Service": "lambda.amazonaws.com"
      },
      "Action": "sts:AssumeRole"
  } ]
}
```

2. Create a policy with this trust policy
```
$ aws iam create-role \
--role-name AutoScaling-GlobalAccelerator-Lambda-Role \
--assume-role-policy-document file://Lambda-Role-Trust-Policy.json
```

#### Inline policy

1. Create a text file called Lambda-Role-Inline-Policy.json with the following content

```
{
	"Version": "2012-10-17",
	"Statement": [{
			"Effect": "Allow",
			"Resource": "*",
			"Action": [
				"autoscaling:CompleteLifecycleAction",
				"sns:Publish"
			]
		},
		{
			"Action": [
				"logs:CreateLogGroup",
				"logs:CreateLogStream",
				"logs:PutLogEvents"
			],
			"Resource": "arn:aws:logs:*:*:*",
			"Effect": "Allow"
		}
	]
}
```

2. Apply the inline policy to the IAM role we just created
```
$ aws iam put-role-policy \
--role-name AutoScaling-GlobalAccelerator-Lambda-Role \
--policy-name AutoScalingGlobalAcceleratorWithLogging \
--policy-document file://Lambda-Role-Inline-Policy.json
```

## Step 3 - Put the lifecycle hooks

### Hook for instance terminating
```
$ aws autoscaling put-lifecycle-hook \
--lifecycle-hook-name ASG-AGA-Hook-Terminating \
--auto-scaling-group-name My-ASG-Group-Name \
--notification-target-arn arn:aws:sns:us-west-2:0123456789012:AutoScaling-GlobalAccelerator-Topic \
--role-arn arn:aws:iam::0123456789012:role/AutoScaling-GlobalAccelerator-Topic-Publisher-Role \
--lifecycle-transition autoscaling:EC2_INSTANCE_TERMINATING \
--heartbeat-timeout 300
```

### Hook for instance launching
```
$ aws autoscaling put-lifecycle-hook \
--lifecycle-hook-name ASG-AGA-Hook-Launching \
--auto-scaling-group-name My-ASG-Group-Name \
--notification-target-arn arn:aws:sns:us-west-2:0123456789012:AutoScaling-GlobalAccelerator-Topic \
--role-arn arn:aws:iam::0123456789012:role/AutoScaling-GlobalAccelerator-Topic-Publisher-Role \
--lifecycle-transition autoscaling:EC2_INSTANCE_LAUNCHING \
--heartbeat-timeout 600
```

## Step 4 - Create the Lambda function

The Lambda function uses modules included in the Python 3.7 Standard Library and the AWS SDK for Python module (boto3), which is preinstalled as part of Lambda. The function code performs the following:

- Gets the EC2 instance ID that is being launched or terminated from the SNS notification,
- Lists the endpoints attached to the endpoint group (describeEndpointGroup API),
- Update the endpoint group (updateEndpointGroup API) by removing the endpoint from the endpoint group if the instance is being terminated OR by adding the endpoint to the endpoint group if the instance is being launched. 
- Check the status of updateEndpointGroup API call and if it fails, the Lambda function completes the lifecycle hook.

1. Open the [Lambda console](https://console.aws.amazon.com/lambda).
2. Choose **Create function**.
3. Configure the following settings:
   - Name: AutoScaling-GlobalAccelerator.
   - Runtime: Python 3.7.
   - Role: Choose an existing role.
   - Existing role: AutoScaling-GlobalAccelerator-Lambda-Role (the role previously created).
4. In Advanced settings, configure **Timeout for 5 minutes.**
5. Choose **Create function.**
6. Your function is created; for it code, copy and paste the [Lambda function](autoscaling_globalaccelerator.py) from this GitHub repository.

## Step 5 - Configuring the SNS topic with the Lambda function
Auto Scaling lifecycle hooks can send notifications to CloudWatch Events, SNS or SQS, we will use Amazon SNS. For this we will need to subscribe the Lambda function to the SNS topic and grant permissions on the lambda function to the SNS topic.

### Subscribe the Lambda function to the SNS topic
```
aws sns subscribe --protocol lambda \
--topic-arn arn:aws:sns:us-west-2:012345678901:ENI-Demo-Topic \
--notification-endpoint arn:aws:lambda:us-west-2:012345678901:function:AutoScaling-GlobalAccelerator
```
### Grant permissions on the lambda function to the SNS topic
```
aws lambda add-permission \
--function-name AutoScaling-GlobalAccelerator \
--statement-id GrantSNSPermission \
--action "lambda:InvokeFunction" \
--principal sns.amazonaws.com \
--source-arn arn:aws:sns:us-west-2:012345678901:AutoScaling-GlobalAccelerator-Topic
```

## Testing and reviewing the logs
To test, simply edit your Auto Scaling group to increase the desired size, causing an instance to be added to the group, and then decrease the desired size to remove instances from the Auto Scaling group. The following CLI command will return the list of the endpoints attached to the endpoint group:

```
$ aws globalaccelerator describe-endpoint-group --endpoint-group-arn arn:aws:globalaccelerator::012345678901:accelerator/c9d8f18d-e6a7-4f28-ae95-261507146530/listener/461df876/endpoint-group/c3770cbbf005 --region us-west-2
```
