#!/usr/bin/env python
#
# Copyright 2016 Confluent Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
import pytest

from confluent_kafka import TopicPartition, KafkaException, KafkaError
from confluent_kafka.error import ConsumeError
from confluent_kafka.schema_registry.protobuf import AsyncProtobufSerializer, AsyncProtobufDeserializer
from tests.integration.schema_registry.data.proto import metadata_proto_pb2, NestedTestProto_pb2, TestProto_pb2, \
    PublicTestProto_pb2
from tests.integration.schema_registry.data.proto.DependencyTestProto_pb2 import DependencyMessage
from tests.integration.schema_registry.data.proto.exampleProtoCriteo_pb2 import ClickCas


@pytest.mark.parametrize("pb2, data", [
    (TestProto_pb2.TestMessage, {'test_string': "abc",
                                 'test_bool': True,
                                 'test_bytes': b'look at these bytes',
                                 'test_double': 1.0,
                                 'test_float': 12.0}),
    (PublicTestProto_pb2.TestMessage, {'test_string': "abc",
                                       'test_bool': True,
                                       'test_bytes': b'look at these bytes',
                                       'test_double': 1.0,
                                       'test_float': 12.0}),
    (NestedTestProto_pb2.NestedMessage, {'user_id':
     NestedTestProto_pb2.UserId(
         kafka_user_id='oneof_str'),
        'is_active': True,
        'experiments_active': ['x', 'y', '1'],
        'status': NestedTestProto_pb2.INACTIVE,
        'complex_type':
            NestedTestProto_pb2.ComplexType(
                one_id='oneof_str',
                is_active=False)})
])
async def test_protobuf_message_serialization(kafka_cluster, pb2, data):
    """
    Validates that we get the same message back that we put in.

    """
    topic = kafka_cluster.create_topic_and_wait_propogation("serialization-proto")
    sr = kafka_cluster.async_schema_registry()

    value_serializer = await AsyncProtobufSerializer(pb2, sr, {'use.deprecated.format': False})
    value_deserializer = await AsyncProtobufDeserializer(pb2, {'use.deprecated.format': False})

    producer = kafka_cluster.async_producer(value_serializer=value_serializer)
    consumer = kafka_cluster.async_consumer(value_deserializer=value_deserializer)
    consumer.assign([TopicPartition(topic, 0)])

    expect = pb2(**data)
    await producer.produce(topic, value=expect, partition=0)
    producer.flush()

    msg = await consumer.poll()
    actual = msg.value()

    assert [getattr(expect, k) == getattr(actual, k) for k in data.keys()]


@pytest.mark.parametrize("pb2, expected_refs", [
    (TestProto_pb2.TestMessage, ['google/protobuf/descriptor.proto']),
    (NestedTestProto_pb2.NestedMessage, ['google/protobuf/timestamp.proto']),
    (DependencyMessage, ['NestedTestProto.proto', 'PublicTestProto.proto']),
    (ClickCas, ['metadata_proto.proto', 'common_proto.proto'])
])
async def test_protobuf_reference_registration(kafka_cluster, pb2, expected_refs):
    """
    Registers multiple messages with dependencies then queries the Schema
    Registry to ensure the references match up.

    """
    sr = kafka_cluster.async_schema_registry()
    topic = kafka_cluster.create_topic_and_wait_propogation("serialization-proto-refs")
    serializer = await AsyncProtobufSerializer(pb2, sr, {'use.deprecated.format': False})
    producer = kafka_cluster.async_producer(key_serializer=serializer)

    await producer.produce(topic, key=pb2(), partition=0)
    producer.flush()

    registered_refs = (await sr.get_schema(serializer._schema_id.id)).references

    assert expected_refs.sort() == [ref.name for ref in registered_refs].sort()


async def test_protobuf_serializer_type_mismatch(kafka_cluster):
    """
    Ensures an Exception is raised when deserializing an unexpected type.

    """
    pb2_1 = TestProto_pb2.TestMessage
    pb2_2 = NestedTestProto_pb2.NestedMessage

    sr = kafka_cluster.async_schema_registry()
    topic = kafka_cluster.create_topic_and_wait_propogation("serialization-proto-refs")
    serializer = await AsyncProtobufSerializer(pb2_1, sr, {'use.deprecated.format': False})

    producer = kafka_cluster.async_producer(key_serializer=serializer)

    with pytest.raises(KafkaException,
                       match=r"message must be of type <class"
                             r" 'tests.integration.schema_registry.data.proto.TestProto_pb2.TestMessage'\> not \<class"
                             r" 'tests.integration.schema_registry.data.proto.NestedTestProto_pb2.NestedMessage'\>"):
        await producer.produce(topic, key=pb2_2())


async def test_protobuf_deserializer_type_mismatch(kafka_cluster):
    """
    Ensures an Exception is raised when deserializing an unexpected type.

    """
    pb2_1 = PublicTestProto_pb2.TestMessage
    pb2_2 = metadata_proto_pb2.HDFSOptions

    sr = kafka_cluster.async_schema_registry()
    topic = kafka_cluster.create_topic_and_wait_propogation("serialization-proto-refs")
    serializer = await AsyncProtobufSerializer(pb2_1, sr, {'use.deprecated.format': False})
    deserializer = await AsyncProtobufDeserializer(pb2_2, {'use.deprecated.format': False})

    producer = kafka_cluster.async_producer(key_serializer=serializer)
    consumer = kafka_cluster.async_consumer(key_deserializer=deserializer)
    consumer.assign([TopicPartition(topic, 0)])

    def dr(err, msg):
        print("dr msg {} {}".format(msg.key(), msg.value()))

    await producer.produce(
        topic,
        key=pb2_1(test_string='abc', test_bool=True, test_bytes=b'def'),
        partition=0
    )
    producer.flush()

    with pytest.raises(ConsumeError) as e:
        await consumer.poll()
    assert e.value.code == KafkaError._KEY_DESERIALIZATION
