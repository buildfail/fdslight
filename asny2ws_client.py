#!/usr/bin/env python3

import any2ws.handlers.any2tcp as any2tcp
import any2ws.handlers.client as client
import any2ws.any2wsd as any2wsd


class any2wsd_client(any2wsd.any2wsd):
    def any2ws_init(self):
        pass

    def any2ws_release(self):
        pass


def main():
    print(any2wsd.BASE_DIR)


if __name__ == '__main__': main()
