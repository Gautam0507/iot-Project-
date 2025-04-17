import logging
import paho.mqtt.client as mqtt
from threading import Thread
import asyncio
from typing import Optional, Callable, Dict, Any
from sensor_data_processor import process_sensor_message


class MQTTHandler:
    def __init__(
        self,
        broker: str = "localhost",
        port: int = 1883,
        client_id: str = "fastapi_client",
        keepalive: int = 60,
        logger: Optional[logging.Logger] = None,
    ):
        # Configuration
        self.broker = broker
        self.port = port
        self.keepalive = keepalive
        self.client_id = client_id
        self.logger = logger or logging.getLogger("mqtt_handler")
        self.subscriptions: Dict[str, Callable] = {}

        # Create client
        self.client = mqtt.Client(client_id=self.client_id)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.on_disconnect = self._on_disconnect

        self._connection_thread: Optional[Thread] = None
        self._is_started = False

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            self.logger.info(f"Connected to MQTT Broker at {self.broker}:{self.port}")
            # Re-subscribe to topics if any
            for topic in self.subscriptions:
                self.client.subscribe(topic)
                self.logger.info(f"Subscribed to topic: {topic}")
        else:
            self.logger.error(f"Failed to connect to MQTT broker with code: {rc}")

    def _on_message(self, client, userdata, msg):
        topic = msg.topic
        payload = msg.payload.decode()
        self.logger.info(f"Received message on {topic}: {payload}")

        # Special handling for sensor data topic
        if topic == "sensors/data":
            self.logger.info("Processing sensor data message")
            processed_data = process_sensor_message(payload, self.logger)

            # Store the processed data for WebSocket broadcasting
            if processed_data:
                from main import set_latest_sensor_data, manager

                set_latest_sensor_data(processed_data)

                # Create a task to broadcast the data to all connected clients
                try:
                    # Get event loop or create a new one
                    try:
                        loop = asyncio.get_event_loop()
                    except RuntimeError:
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)

                    # Create a task to broadcast the data
                    loop.create_task(manager.broadcast(processed_data))
                    self.logger.info("Scheduled WebSocket broadcast of new sensor data")
                except Exception as e:
                    self.logger.error(f"Error scheduling WebSocket broadcast: {str(e)}")

        # Call any registered handlers for this topic
        if topic in self.subscriptions and self.subscriptions[topic]:
            try:
                self.subscriptions[topic](topic, payload)
            except Exception as e:
                self.logger.error(f"Error in message handler for {topic}: {str(e)}")

    def _on_disconnect(self, client, userdata, rc):
        self.logger.warning(f"Disconnected from MQTT broker with code: {rc}")

    def start(self):
        """Start the MQTT client in a non-blocking way"""
        if self._is_started:
            return

        def connect_mqtt():
            try:
                self.logger.info(
                    f"Connecting to MQTT broker at {self.broker}:{self.port}..."
                )
                self.client.connect(self.broker, self.port, self.keepalive)
                self.client.loop_start()
            except Exception as e:
                self.logger.error(f"Failed to connect to MQTT broker: {str(e)}")

        self._connection_thread = Thread(target=connect_mqtt)
        self._connection_thread.daemon = True
        self._connection_thread.start()
        self._is_started = True

    def stop(self):
        """Stop the MQTT client"""
        if self._is_started:
            self.logger.info("Stopping MQTT client...")
            self.client.loop_stop()
            self.client.disconnect()
            self._is_started = False

    def subscribe(self, topic: str, callback: Optional[Callable] = None):
        """Subscribe to an MQTT topic with an optional callback"""
        self.subscriptions[topic] = callback
        if self.is_connected():
            self.client.subscribe(topic)
            self.logger.info(f"Subscribed to topic: {topic}")
            return True
        return False

    def publish(self, topic: str, payload: str) -> bool:
        """Publish a message to an MQTT topic"""
        if not self.is_connected():
            self.logger.warning("Cannot publish: not connected to MQTT broker")
            return False

        result = self.client.publish(topic, payload)
        if result.rc == mqtt.MQTT_ERR_SUCCESS:
            self.logger.info(f"Published message to {topic}: {payload}")
            return True
        else:
            self.logger.error(f"Publish failed with code {result.rc}")
            return False

    def is_connected(self) -> bool:
        """Check if connected to the MQTT broker"""
        return self.client.is_connected() if self._is_started else False
