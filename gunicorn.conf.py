import eventlet
eventlet.monkey_patch()

bind = "0.0.0.0:10000"
workers = 1
worker_class = "eventlet"
timeout = 120
graceful_timeout = 30
keepalive = 5