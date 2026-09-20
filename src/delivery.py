"""Explicit publish → verify → accepted send → durable marker boundary."""
import logging

def deliver(session, publish, verify, send, mark):
    image_url = publish()
    logging.info('public_image_url=%s', image_url)
    verify(image_url)
    receipt = send()
    if not isinstance(receipt, dict) or receipt.get('code') != 0:
        raise RuntimeError('ServerChan did not acknowledge success; session unchanged')
    logging.info('serverchan_result=accepted session=%s', session)
    mark(session)
    logging.info('last_sent_session=%s persisted=true', session)
    return receipt
