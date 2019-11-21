This CloudFormation template:
- creates an IAM role
- creates a Lambda funtion that automatically updates AWS Global Accelerator EC2 endpoints based on Autoscaling groups events
- puts a lifecycle hook for instance terminating to your Autoscaling group.
- create a CloudWatch Rule that trigers the Lambda function everytime your Autoscaling group terminates or launches an EC2 instance.
