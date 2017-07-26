#!/usr/bin/env python
"""
USAGE:
    sqs_cat.py <arn> [--regex=<regex>]... [--subject=<subject>]...
"""

import boto3
import logging
import json

log = logging.getLogger("sqs_cat")
logging.basicConfig()
log.setLevel(logging.DEBUG)



def main(args):
    log.debug(args)

    arn = args["<arn>"]
    arnbits= arn.split(":")

    queue = arnbits[5]
    region = arnbits[3]
    account = arnbits[4]

    subjects = args["--subject"]

    log.debug("sqs topic {} account {} region {}".format(queue, account, region))
    sqs = boto3.session.Session().client("sqs", region_name=region)

    url = sqs.get_queue_url(QueueName=queue)["QueueUrl"]
    log.debug("sqs url {}".format(url))

    while 42:
        msgs = sqs.receive_message(QueueUrl=url, MaxNumberOfMessages=1, WaitTimeSeconds=10)
        for msg in msgs.get("Messages", []):
            # log.debug(json.dumps(msg, indent=2))
            # id = msg["MessageId"]
            body = msg["Body"]
            j = json.loads(body)
            # log.debug(json.dumps(j, indent=2))

            # attributes = msg.get("MessageAttributes", {})

            if len(subjects):
                if j["Subject"] in subjects:
                    log.debug(j["Message"])
            else:
                log.debug(j["Message"])

            sqs.delete_message(QueueUrl=url, ReceiptHandle=msg["ReceiptHandle"])

if __name__ == "__main__":
    from docopt import docopt

    main(docopt(__doc__))