import logging
import threading
import time

import prometheus_client
import requests

TASKS_QUEUE = prometheus_client.Gauge(
    'celery_tasks_by_queue',
    'Number of tasks per queue',
    ['flower', 'queue']
)
WORKERS = prometheus_client.Gauge(
    'celery_workers',
    'Number of alive workers',
    ['flower', 'status']
)
TASKS_WORKER = prometheus_client.Gauge(
    'celery_tasks_by_worker',
    'Number of tasks per worker',
    ['flower', 'worker', 'status']
)


class MonitorThread(threading.Thread):

    def __init__(self, flower_host, *args, **kwargs):
        self.flower_host = flower_host
        self.log = logging.getLogger(f'monitor.{flower_host}')
        self.log.info('Setting up monitor thread')
        self.log.debug(f"Running monitoring thread for {self.flower_host} host.")
        self.setup_metrics()
        super().__init__(*args, **kwargs)

    def setup_metrics(self):
        raise NotImplementedError

    def get_metrics(self):
        while True:
            self.log.debug(f"Getting workers data from {self.flower_host}")
            try:
                data = requests.get(self.endpoint)
            except requests.exceptions.ConnectionError as e:
                self.log.error(f'Error receiving data from {self.flower_host} - {e}')
                time.sleep(5)
                continue
            if data.status_code != 200:
                self.log.error(f'Error receiving data from {self.flower_host}. '
                               f'Host responded with HTTP {data.status_code}')
                time.sleep(1)
                continue
            self.convert_data_to_prometheus(data.json())
            time.sleep(1)

    @property
    def endpoint(self):
        raise NotImplementedError

    def convert_data_to_prometheus(self, data):
        raise NotImplementedError

    def run(self):
        self.log.info(f'Running monitor thread for {self.flower_host}')
        self.get_metrics()


class QueueMonitorThread(MonitorThread):
    def setup_metrics(self):
        logging.info("Setting metrics up")
        for metric in TASKS_QUEUE.collect():
            for sample in metric.samples:
                TASKS_QUEUE.labels(**sample[1]).set(0)

    @property
    def endpoint(self):
        return self.flower_host + '/api/queues/length'

    def convert_data_to_prometheus(self, data):
        for q_info in data.get('active_queues', []):
            TASKS_QUEUE.labels(flower=self.flower_host, queue=q_info['name']).set(q_info['messages'])


class WorkerMonitorThread(MonitorThread):
    def setup_metrics(self):
        logging.info("Setting metrics up")
        for metric in WORKERS.collect():
            for sample in metric.samples:
                WORKERS.labels(**sample[1]).set(0)
        for metric in TASKS_WORKER.collect():
            for sample in metric.samples:
                TASKS_WORKER.labels(**sample[1]).set(0)

    @property
    def endpoint(self):
        return self.flower_host + '/dashboard?json=1'

    def convert_data_to_prometheus(self, data):
        online, offline = 0, 0
        for w_info in data.get('data', []):
            common = {'flower': self.flower_host, 'worker': w_info['hostname']}

            TASKS_WORKER.labels(**common, status='received').set(w_info.get('task-received', 0))
            TASKS_WORKER.labels(**common, status='started').set(w_info.get('task-started', 0))
            TASKS_WORKER.labels(**common, status='failed').set(w_info.get('task-failed', 0))
            TASKS_WORKER.labels(**common, status='retried').set(w_info.get('task-retried', 0))
            TASKS_WORKER.labels(**common, status='succeeded').set(w_info.get('task-succeeded', 0))

            TASKS_WORKER.labels(**common, status='processed').set(w_info.get('processed', 0))
            TASKS_WORKER.labels(**common, status='active').set(w_info.get('active', 0))

            if w_info['status']:
                online += 1
            else:
                offline += 1

        WORKERS.labels(flower=self.flower_host, status='online').set(online)
        WORKERS.labels(flower=self.flower_host, status='offline').set(offline)
