"""HTTP to Kafka Relay."""
import base64
import json
import logging
import os
from typing import List, Union

from aiokafka import AIOKafkaProducer

from fastapi import Depends, FastAPI, Request, Response

DEFAULT_BOOTSTRAP_SERVER = "kafka"
DEFAULT_INBOUND_TOPIC = "acapy-inbound-message"
KAFKA_BOOTSTRAP = os.environ.get("KAFKA_BOOTSTRAP", DEFAULT_BOOTSTRAP_SERVER)
INBOUND_TOPIC = os.environ.get("INBOUND_TOPIC", DEFAULT_INBOUND_TOPIC)

app = FastAPI(title="HTTP to Kafka Relay", version="0.1.0")
LOGGER = logging.getLogger("uvicorn.error." + __name__)


class ProducerDependency:
    """Hold a single producer across requests."""

    def __init__(self):
        """Create Dependency."""
        self.producer = AIOKafkaProducer(
            bootstrap_servers=KAFKA_BOOTSTRAP, enable_idempotence=True
        )
        self.started = False

    async def __call__(self) -> AIOKafkaProducer:
        """Retrieve producer."""
        return self.producer


producer_dep = ProducerDependency()


@app.on_event("startup")
async def start_producer():
    """Start up kafka producer on startup."""
    LOGGER.info("Starting Kafka Producer...")
    await producer_dep.producer.start()
    LOGGER.info("Kafka Producer started")


@app.on_event("shutdown")
async def stop_producer():
    """Stop producer on shutdown."""
    LOGGER.info("Stopping Kafka Producer...")
    await producer_dep.producer.stop()
    LOGGER.info("Kafka Producer stopped")


def b64_to_bytes(val: Union[str, bytes], urlsafe=False) -> bytes:
    """Convert a base 64 string to bytes."""
    if isinstance(val, str):
        val = val.encode("ascii")
    if urlsafe:
        missing_padding = len(val) % 4
        if missing_padding:
            val += b"=" * (4 - missing_padding)
        return base64.urlsafe_b64decode(val)
    return base64.b64decode(val)


def _recipients_from_packed_message(packed_message: bytes) -> List[str]:
    """
    Inspect the header of the packed message and extract the recipient key.
    """
    try:
        wrapper = json.loads(packed_message)
    except Exception as err:
        raise ValueError("Invalid packed message") from err

    recips_json = b64_to_bytes(wrapper["protected"], urlsafe=True).decode("ascii")
    try:
        recips_outer = json.loads(recips_json)
    except Exception as err:
        raise ValueError("Invalid packed message recipients") from err

    return [recip["header"]["kid"] for recip in recips_outer["recipients"]]


@app.post("/")
async def receive_message(
    request: Request, producer: AIOKafkaProducer = Depends(producer_dep)
):
    """Receive a new agent message and post to Kafka."""
    message = await request.body()
    LOGGER.debug("Received message, pushing to Kafka: %s", message)

    recips = ",".join(_recipients_from_packed_message(message)).encode("utf8")
    LOGGER.info(
        f"Sending Kafka event with topic: {INBOUND_TOPIC}, message: {message}, "
        f"key: {recips[0]}"
    )
    await producer.send_and_wait(INBOUND_TOPIC, message, key=recips)
    return Response(status_code=200)
