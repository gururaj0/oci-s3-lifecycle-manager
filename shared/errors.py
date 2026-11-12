class Problem(Exception):
    def __init__(self, status, code, lease_id=None):
        self.status = status
        self.code = code
        self.lease_id = lease_id
        super().__init__(code)
