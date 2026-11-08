class ServiceError(ValueError):
    def __init__(self, code, message=None, **details):
        self.code, self.message, self.details = code, message or code, details
        super().__init__(self.message)

    def result(self):
        return {"code": self.code, "message": self.message, "retryable": self.code == "busy", **self.details}
