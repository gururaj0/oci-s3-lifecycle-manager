"""Run independently from the API under a systemd timer; same durable ownership checks."""
from shared.runtime import engine
if __name__=='__main__':
    engine().reconcile()
