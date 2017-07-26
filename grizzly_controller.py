#!/usr/bin/env python

"""
Usage:
    grizzly_controller.py start [--region=<region>...]  <attack> <threads> <instances> <ttl> <time>
    grizzly_controller.py stop [--region=<region>...]
    grizzly_controller.py delete [--region=<region>...]
    grizzly_controller.py kill [--region=<region>...]
    grizzly_controller.py pushconfig
    grizzly_controller.py pushfiles <manifest>

Options:
    --region=<region>, -r <region>, --region <region>     which region to launch in.  Will launch the same number in each region
"""

import logging
import boto3
from docopt import docopt
import json
from botocore.exceptions import ClientError
import os.path
import io
import zipfile
import uuid

AMI_MAP ={
    "us-west-2": "ami-6e1a0117",
    "ap-south-1": "ami-099fe766",
    "us-east-1": "ami-cd0f5cb6",
    "eu-west-2": "ami-996372fd",
    "ap-southeast-2": "ami-e2021d81",
    "us-east-2": "ami-10547475",
    "us-west-1": "ami-09d2fb69",
    "sa-east-1": "ami-10186f7c",
    "ca-central-1": "ami-9818a7fc",
    "ap-northeast-1": "ami-ea4eae8c",
    "eu-west-1": "ami-785db401",
    "ap-northeast-2": "ami-d28a53bc",
    "eu-central-1": "ami-1e339e71",
    "ap-southeast-1": "ami-6f198a0c"
}

DEFAULT_REGION = "us-west-2"

GRIZZLY_BUCKET = "yourbucket-repulsive-grizzly"
BUCKET_REGION = "us-west-2"
LAUNCH_CONFIG_FILE = "grizzly_launch_config.json"
CLOUD_INIT_FILE = "repulsive_grizzly.cloud-init.yaml"
COMMAND_FILE = "commands.json"
COMMAND_TEMPLATE_FILE="grizzly.commandfile.template.json"
CONFIG_PREFIX="cc"
BASE_NAME = "repulsive_grizzly"
ZIPFILE = "grizzly.zip"
SHELL_SCRIPT="grizzly_runner.sh"

log = logging.getLogger("grizzly_controller")
logging.basicConfig()
log.setLevel(logging.DEBUG)


def get_file_data(path):
    return json.loads(get_file(path))


def get_file(path):
    s3 = boto3.session.Session().client("s3", BUCKET_REGION)

    if not path.startswith("s3://"):
        raise RuntimeError("Invalid bucket path '{}'".format(path))

    bucket, key = path[5:].split("/",1)

    obj = s3.get_object(Bucket=bucket, Key=key)
    data = obj["Body"].read()

    return data


def get_vpcid(vpcname, region):
    vpc = boto3.session.Session().client("ec2", region_name=region)
    vpcs = vpc.describe_vpcs(Filters=[{"Name": "tag:Name", "Values": [vpcname]}])
    if len(vpcs['Vpcs']) != 1:
        raise RuntimeError("Can't find vpc {} in region {}".format(vpcname, region))
    vpcid = vpcs['Vpcs'][0]["VpcId"]

    log.debug("vpc {} in region {} is {}".format(vpcname, region, vpcid))

    return vpcid


def get_sgid(sgname, vpcid, region):
    ec2 = boto3.session.Session().client("ec2", region_name=region)

    sgs = ec2.describe_security_groups(Filters=[{"Name": "vpc-id", "Values": [vpcid]},
                                                {"Name": "group-name", "Values": [sgname]}])

    if len(sgs['SecurityGroups']) != 1:
        log.debug("search for {} in {} returned {}".format(sgname, region, sgs))

    sgid= sgs["SecurityGroups"][0]["GroupId"]
    log.debug("{} in vpc {} region {} is {}".format(sgname, vpcid, region, sgid))

    return sgid


def create_launch_config(region, batchid):
    asg = boto3.session.Session().client("autoscaling", region_name=region)
    name = "{}_config".format(BASE_NAME)

    configs = asg.describe_launch_configurations(LaunchConfigurationNames=[name])
    if len(configs["LaunchConfigurations"]):
        log.debug("launch config {} exists. exiting".format(name))
        return None

    lc_key ="s3://{bucket}/{prefix}/{file}".format(bucket=GRIZZLY_BUCKET, prefix=CONFIG_PREFIX, file=LAUNCH_CONFIG_FILE)
    log.debug("loading launch config base {}".format(lc_key))
    lc_base = get_file_data(lc_key)

    vpcid = get_vpcid(lc_base["vpc"], region)
    log.debug("vpc: {}".format(vpcid))
    sgid = get_sgid(lc_base["security_group"], vpcid, region)

    ud_key = "s3://{bucket}/{prefix}/{file}".format(bucket=GRIZZLY_BUCKET, prefix=CONFIG_PREFIX, file=CLOUD_INIT_FILE)

    launch_config = {
        "LaunchConfigurationName": name,
        "KeyName": lc_base["key_name"],
        "SecurityGroups": [sgid],
        "ImageId": AMI_MAP[region],
        "InstanceType": lc_base["instance_type"],
        "IamInstanceProfile": lc_base["instance_profile"],
        "AssociatePublicIpAddress": True,
        "UserData": get_file(ud_key).format(BATCHID=batchid)
    }

    log.debug("launch config: {}".format(launch_config))

    lc = asg.create_launch_configuration(**launch_config)

    config = {"vpcid": vpcid, "sgid": sgid, "launchconfig": name, "config_base": lc_base}

    return config

def get_subnets(region, vpcid):
    ec2 = boto3.session.Session().client("ec2", region_name=region)
    subnets = ec2.describe_subnets(Filters=[{"Name": "tag:Name", "Values": ["{}.external*".format(BASE_NAME)]},
                                            {"Name": "vpc-id", "Values": [vpcid]}])

    subnetids = ",".join(map(lambda x: x["SubnetId"], subnets["Subnets"]))
    log.debug("subnetids: {}".format(subnetids))

    return subnetids


def create_asg(config, attack, ninstances, nthreads, region):
    asg = boto3.session.Session().client("autoscaling", region_name=region)

    name = "{}_asg".format(BASE_NAME)

    asgs = asg.describe_auto_scaling_groups(AutoScalingGroupNames=[name])


    subnets = get_subnets(region, config['vpcid'])
    config["subnets"] = subnets

    asg_config = dict(AutoScalingGroupName=name,
                      LaunchConfigurationName=config["launchconfig"],
                      MinSize=0,
                      MaxSize=ninstances,
                      DesiredCapacity=ninstances,
                      VPCZoneIdentifier=subnets,
                      DefaultCooldown=0
    )


    if len(asgs["AutoScalingGroups"]) == 1:
        log.debug("asg {} already exists in {}, updating".format(name, region))
        r = asg.update_auto_scaling_group(**asg_config)
    else:
        log.debug("Creatinging asg {} in {}".format(name, region))
        r = asg.create_auto_scaling_group(**asg_config)

    log.debug(r)

    return config


def reset_node_counter(region):
    d = boto3.session.Session().client("dynamodb", region_name="us-west-2")
    d.put_item(TableName=BASE_NAME,
               Item={"key": {"S": "counter"}, "node_number": {"N": "0"}, "region": {"S": region}})


def create_command_file(attack, nthreads, ttl, start_time):
    key = "s3://{b}/{p}/{f}".format(b=GRIZZLY_BUCKET, p=CONFIG_PREFIX, f=COMMAND_TEMPLATE_FILE)
    template = get_file(key)
    command_file = template.format(attack=int(attack),
                                   ttl=ttl,
                                   threads=nthreads,
                                   start_time=start_time)

    log.debug("template: {}".format(template))

    log.debug("command_file: {}".format(command_file))
    key = "s3://{b}/{p}/{f}".format(b=GRIZZLY_BUCKET, p=CONFIG_PREFIX, f=COMMAND_FILE)

    log.debug("updating {}".format(key))
    put_file(key, command_file)


def start_instances(attack, nthreads, ninstances, regions, ttl, start_time):
    log.info("Starting instances for attack '{attack}' on {ninst} instances and {nthread} threads".format(
        attack=attack, ninst=ninstances, nthread=nthreads))

    create_command_file(attack, nthreads, ttl, start_time)
    set_kill_switch(False)

    batchid = uuid.uuid4()
    reset_node_counter("all")

    for region in regions:
        reset_node_counter(region)
        config = create_launch_config(region, batchid=batchid)
        if config:
            config = create_asg(config, attack, ninstances, nthreads, region)


def stop_instances(regions):
    for region in regions:

        name = "{}_asg".format(BASE_NAME)
        log.debug("Shutting down asg {} in {}".format(name, region))

        asg = boto3.session.Session().client("autoscaling", region_name=region)
        asgs = asg.describe_auto_scaling_groups(AutoScalingGroupNames=[name])
        if len(asgs["AutoScalingGroups"]) < 1:
            log.debug("No asg named {} in region {}".format(name, region))
        else:
            instances = map(lambda x: x["InstanceId"], asgs["AutoScalingGroups"][0]["Instances"])
            log.debug("ASG {} in {} has {} instances running".format(name, region, len(instances)))
            log.debug("Setting ASG scaling to 0")
            r = asg.update_auto_scaling_group(AutoScalingGroupName=name, DesiredCapacity=0, MaxSize=0)
            log.debug(r)

            if instances:
                ec2 = boto3.session.Session().client("ec2", region_name=region)

                log.debug("Terminating instances {} in region {}".format(instances, region))

                r = ec2.terminate_instances(InstanceIds=instances)
                log.debug(r)
    log.debug("Setting killswitch true")
    set_kill_switch(True)


def delete_instances(regions):
    for region in regions:

        name = "{}_asg".format(BASE_NAME)
        asg = boto3.session.Session().client("autoscaling", region_name=region)

        log.debug("Removing ASG {} in {}".format(name, region))

        asgs = asg.describe_auto_scaling_groups(AutoScalingGroupNames=[name])
        if len(asgs["AutoScalingGroups"]) < 1:
            log.debug("No asg named {} in region {}".format(name, region))
        else:
            r = asg.update_auto_scaling_group(AutoScalingGroupName=name, DesiredCapacity=0, MaxSize=0)
            log.debug(r)

        try:
            r = asg.delete_auto_scaling_group(AutoScalingGroupName=name)
            log.debug(r)
        except ClientError as ce:
            if "ValidationError" in ce.args:
                log.debug("asg {} already deleted".format(name))

        name = "{}_config".format(BASE_NAME)
        log.debug("Deleting launch config {} in {}".format(name, region))
        configs = asg.describe_launch_configurations(LaunchConfigurationNames=[name])
        if len(configs["LaunchConfigurations"]):
            log.debug("launch config {} exists.  Removing".format(name))
            asg.delete_launch_configuration(LaunchConfigurationName=name)


def set_kill_switch(switch=True):
    d = boto3.session.Session().client("dynamodb", region_name="us-west-2")
    log.debug("Setting kill switch to {}".format(switch))
    d.put_item(TableName=BASE_NAME, Item={"key": {"S": "kill_switch"}, "shutdown": {"BOOL": switch}, "region": {"S": "all"}})


def kill_instances():
    set_kill_switch(True)


def put_file(key, body):
    if not key.startswith("s3://"):
        raise RuntimeError("invalid s3 path")

    s3 = boto3.session.Session().client("s3", region_name=BUCKET_REGION)

    bucket, path = key[5:].split("/", 1)
    log.debug("uploading to {}".format(key))

    r = s3.put_object(Bucket=bucket, Key=path, Body=body)


def push_config():
    log.debug("Pushing config files to s3")
    for fn in [LAUNCH_CONFIG_FILE, CLOUD_INIT_FILE, COMMAND_TEMPLATE_FILE]:
        key = "s3://{b}/{p}/{k}".format(b=GRIZZLY_BUCKET, p=CONFIG_PREFIX, k=fn)
        log.debug("updating {}".format(key))
        with open(fn) as fd:
            put_file(key, fd.read())


def push_files(manifest):
    manifest_path = os.path.abspath(manifest)
    path = os.path.dirname(manifest_path)

    log.debug("creating zip from manifest{m}".format(m=manifest_path))

    zipfd = io.BytesIO()

    with open(manifest_path) as fd:
        manifest = json.load(fd)

    with zipfile.ZipFile(zipfd, "w") as zip:
        for fn in manifest["files"]:
            log.debug("Adding {} to zipfile".format(fn))
            zip.write("{}/{}".format(path, fn), fn)

    zipfd.seek(0)
    put_file("s3://{b}/{n}".format(b=GRIZZLY_BUCKET, n=ZIPFILE), zipfd.read())

    with open(SHELL_SCRIPT) as fd:
        put_file("s3://{b}/{p}/{n}".format(b=GRIZZLY_BUCKET, p=CONFIG_PREFIX, n=SHELL_SCRIPT), fd.read())

    push_config()

def main(args):

    log.debug(args)

    nthreads = args["<threads>"] and int(args["<threads>"])
    ttl= args["<ttl>"] and int(args["<ttl>"])
    attack= args["<attack>"] and int(args["<attack>"])
    ninst = args["<instances>"] and int(args["<instances>"])
    regions = args.get("--region")
    if not len(regions):
        regions = [DEFAULT_REGION]
    start_time = args["<time>"]

    if args["start"]:
        log.debug("Starting instances")
        start_instances(attack, nthreads, ninst, regions, ttl, start_time)
    elif args["stop"]:
        log.debug("Stopping instances")
        stop_instances(regions)
    elif args["delete"]:
        log.debug("Delete instances")
        delete_instances(regions)
    elif args["pushconfig"]:
        push_config()
    elif args["kill"]:
        log.debug("Setting kill switch true")
        kill_instances()
    elif args["pushfiles"]:
        manifest = args["<manifest>"]
        push_files(manifest)
    else:
        log.error("unhandled arguments: {}".format(args))

if __name__ == "__main__":
    main(docopt(__doc__))
