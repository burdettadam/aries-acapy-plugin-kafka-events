"""ACA-Py Event to Kafka Bridge."""

import json
import logging
import re
import asyncio

from aiokafka import AIOKafkaProducer, AIOKafkaConsumer
from aries_cloudagent.config.injection_context import InjectionContext
from aries_cloudagent.core.event_bus import Event, EventBus
from aries_cloudagent.core.profile import Profile

# from .aio_consumer import AIOConsumer
# TODO: combine these regular expressions into a single regex
EVENT_PATTERN_WEBHOOK = re.compile("^acapy::webhook::(.*)$")
EVENT_PATTERN_RECORD = re.compile("^acapy::record::([^:]*)(?:::.*)?$")
OUTBOUND_PATTERN = re.compile("acapy::outbound::message$")  # For Event Bus
INBOUND_PATTERN = re.compile("acapy-inbound-.*")  # For Kafka Consumer
BASIC_MESSAGE_PATTERN = re.compile("acapy::basicmessage::.*")
LOGGER = logging.getLogger(__name__)
TOPICS = []
DEFAULT_CONFIG = {"bootstrap_servers": "kafka"}

async def setup(context: InjectionContext):
    """Setup the plugin."""

    try:
        producer_conf = context.settings["plugin_config"]["kafka_queue"][
            "producer-config"
        ]
    except KeyError:
        producer_conf = DEFAULT_CONFIG
    producer = AIOKafkaProducer(**producer_conf)
    await producer.start()
    context.injector.bind_instance( # Add the Kafka producer in the context
        AIOKafkaProducer, producer
    ) 
    # Handle event for Kafka
    bus = context.inject(EventBus)
    bus.subscribe(EVENT_PATTERN_WEBHOOK, handle_event)
    bus.subscribe(BASIC_MESSAGE_PATTERN, handle_event)
    bus.subscribe(OUTBOUND_PATTERN, handle_event)
    bus.subscribe(EVENT_PATTERN_RECORD, handle_event)
    loop = asyncio.get_event_loop()
    try:
      consumer_conf = context.settings["plugin_config"]["kafka_queue"][
          "consumer-config"
      ]
    except KeyError:
        consumer_conf = {"bootstrap_servers": "kafka", "group_id": "aca-py-events"}
    consumer = AIOKafkaConsumer(**consumer_conf)
    await consumer.start()
    consumer.subscribe(pattern=INBOUND_PATTERN)
    async def consume():
      async for msg in consumer:
        topic = str(msg.topic).replace("-", "::")
        await bus.notify(topic, json.loads(msg.value))

    loop.create_task(consume())

async def handle_event(profile: Profile, event: Event):
    """
    Handle events, passing them off to Kafka.

    Events originating from ACA-Py will be namespaced with `acapy`; for example:

        acapy::record::present_proof::presentation_received

    There are two primary namespaces of ACA-Py events.
    - `record` corresponding to events generated by updates to records. These
      follow the pattern:

        acapy::record::{RECORD_TOPIC}

      This pattern corresponds to records that do not hold a state.
      For stateful records, the following pattern is used:

        acapy::record::{RECORD_TOPIC}::{STATE}

      A majority of records are stateful.
    - `webhook` corresponding to events originally sent only by webhooks or
      that should be sent via webhook. These are emitted by code that has not
      yet been updated to use the event bus. These events should be relatively
      infrequent.
    """
    producer = profile.inject(AIOKafkaProducer)
    LOGGER.info("Handling Kafka producer event: %s", event)
    topic = event.topic.replace("::", "-")
    payload = event.payload
    payload["wallet_id"] = profile.settings.get("wallet.id")
    try:

        LOGGER.info(f"Sending message {payload} with Kafka topic {topic}")
        await producer.send_and_wait(
            topic, str.encode(json.dumps(payload))
        )  # Produce message
    except Exception as exc:
        LOGGER.error(f"Kafka producer failed sending a message due to: {exc}")
