This CloudFormation template:
- creates an IAM role
- creates a Lambda funtion
- puts a lifecycle hook for instance terminating to your Autoscaling group.
- create a CloudWatch Rule that trigers the Lambda function everytime your Autoscaling group terminates or launches an EC2 instance
