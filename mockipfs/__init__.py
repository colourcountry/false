#!/usr/bin/python3

import os, uuid, rdflib, logging

class MockIPFS:
    def __init__(self, file_base, url_base):
        self.base = os.path.join(file_base, 'blob')
        self.namespace = rdflib.Namespace("%s/%s/" % (url_base, 'blob'))
        os.makedirs(self.base, exist_ok=True)

    def add_bytes(self, blob):
        r = str(uuid.uuid4())
        logging.debug("Writing blob %s" % r)
        with open(os.path.join(self.base, r), 'wb') as f:
            f.write(blob)
        return r

    def cat(self, r):
        logging.debug("Reading blob %s" % r)
        with open(os.path.join(self.base, r[-36:]), 'rb') as f:
            return f.read()
