#!/bin/bash

echo setting up environment in /repulsive_grizzly
mkdir /repulsive_grizzly
cd /repulsive_grizzly

echo grabbing files from s3
aws s3 cp s3://yourbucket-repulsive-grizzly/grizzly.zip .
aws s3 cp s3://yourbucket-repulsive-grizzly/cc/commands.json .
unzip grizzly.zip

echo configuring python
pip install -r requirements.txt

echo finding region and instance
REGION=`curl -s http://169.254.169.254/latest/meta-data/placement/availability-zone | sed 's/[a-z]$//'`
INSTANCE=`curl -s http://169.254.169.254/latest/meta-data/instance-id`
TS=`date --iso=seconds`
IP=`curl -s http://checkip.amazonaws.com`

echo instance $INSTANCE running in $REGION started at about $TS

LOG="grizzly_output.$BATCHID.$INSTANCE.$REGION.$TS.log"
echo $LOG

python grizzly_util.py sendmsg arn:aws:sns:us-west-2:123456789012:grizzly control \{\"event\": \"starting\", \"instance\": \"$INSTANCE\", \"region\": \"$REGION\" \"timestamp\": \"$TS\", \"batch\", \"$BATCHID\"\, \"ip\", \"$IP\"}
script $LOG --command "python grizzly.py"
python grizzly_util.py sendmsg arn:aws:sns:us-west-2:123456789012:grizzly control \{\"event\": \"completed\", \"instance\": \"$INSTANCE\", \"region\": \"$REGION\" \"timestamp\": \"$TS\", \"batch\", \"$BATCHID\"\, \"ip\", \"$IP\"}

aws s3 cp $LOG s3://yourbucket-repulsive-grizzly/logs/$LOG
