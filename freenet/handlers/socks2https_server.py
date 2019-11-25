#!/usr/bin/env python3

import pywind.evtframework.handlers.tcp_handler as tcp_handler
import pywind.evtframework.handlers.udp_handler as udp_handler
import pywind.web.lib.websocket as ws
import pywind.web.lib.httputils as httputils
import pywind.lib.timer as timer

import socket, time, urllib.parse, random, os

import freenet.lib.socks2https as socks2https


class listener(tcp_handler.tcp_handler):
    __is_ipv6 = None

    def init_func(self, creator_fd, address, is_ipv6=False):
        self.__is_ipv6 = is_ipv6
        if is_ipv6:
            fa = socket.AF_INET6
        else:
            fa = socket.AF_INET

        s = socket.socket(fa, socket.SOCK_STREAM)
        if is_ipv6: s.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        self.set_socket(s)
        self.bind(address)
        self.listen(10)
        self.register(self.fileno)
        self.add_evt_read(self.fileno)

        return self.fileno

    def tcp_accept(self):
        while 1:
            try:
                cs, caddr = self.accept()
            except BlockingIOError:
                break
            self.create_handler(self.fileno, handler, cs, caddr, is_ipv6=self.__is_ipv6)

    def tcp_error(self):
        self.delete_handler(self.fileno)

    def tcp_delete(self):
        self.unregister(self.fileno)
        self.close()


class handler(tcp_handler.tcp_handler):
    # 是否已经成功握手
    __handshake_ok = None
    __caddr = None

    __packet_id_map = None

    __parser = None
    __builder = None

    __time = None
    # 客户端传送过来的窗口大小
    __win_size = None
    __my_win_size = None
    __qos = None

    def init_func(self, creator_fd, cs, caddr, is_ipv6=False):
        self.__handshake_ok = False
        self.__caddr = caddr
        self.__packet_id_map = {}

        self.__parser = socks2https.parser()
        self.__builder = socks2https.builder()
        self.__qos = socks2https.qos()

        if is_ipv6:
            self.__win_size = 1140
            self.__my_win_size = 1140
        else:
            self.__win_size = 1280
            self.__my_win_size = 1280

        self.__time = time.time()

        self.set_socket(cs)
        self.register(self.fileno)
        self.add_evt_read(self.fileno)

        return self.fileno

    def do_handshake(self):
        size = self.reader.size()
        rdata = self.reader.read()
        p = rdata.find(b"\r\n\r\n")

        if p < 5 and size > 2048:
            self.delete_handler(self.fileno)
            return

        s = rdata.decode("iso-8859-1")
        try:
            rq, kv = httputils.parse_htt1x_request_header(s)
        except httputils.Http1xHeaderErr:
            self.delete_handler(self.fileno)
            return

        m, uri, ver = rq

        if ver.lower() != "http/1.1":
            self.delete_handler(self.fileno)
            return

        if m != "GET":
            self.delete_handler(self.fileno)
            return

        upgrade = self.get_kv_value(kv, "upgrade")
        if not upgrade:
            self.send_403_response()
            return
        if upgrade.lower() != "websocket":
            self.send_403_response()
            return

        connection = self.get_kv_value(kv, "connection")
        if not connection:
            self.send_403_response()
            return
        if connection.lower() != "upgrade":
            self.send_403_response()
            return

        sec_ws_key = self.get_kv_value(kv, "sec-websocket-key")
        if not sec_ws_key:
            self.send_403_response()
            return

        origin = self.get_kv_value(kv, "origin")
        if not origin:
            self.send_403_response()
            return
        ws_ver = self.get_kv_value(kv, "sec-websocket-version")

        try:
            v = int(ws_ver)
        except ValueError:
            self.send_403_response()
            return

        if v != 13:
            self.send_403_response()
            return

        sec_ws_proto = self.get_kv_value(kv, "sec-websocket-protocol")
        if not sec_ws_proto:
            self.send_403_response()
            return

        resp_headers = [
            ("Content-Length", "0"),
        ]

        # 验证用户是否合法
        if not self.auth_user(uri):
            self.send_403_response()
            return

        resp_headers += [("Connection", "Upgrade",), ("Upgrade", "websocket",)]
        resp_headers += [("Sec-WebSocket-Accept", ws.gen_handshake_key(sec_ws_key))]
        resp_headers += [("Sec-WebSocket-Protocol", "socks2https")]

        self.__handshake_ok = True
        self.send_response("101 Switching Protocols", resp_headers)

    def send_response(self, status, headers):
        s = httputils.build_http1x_resp_header(status, headers)
        byte_data = s.encode("iso-8859-1")

        self.writer.write(byte_data)
        self.add_evt_write(self.fileno)

    def send_403_response(self):
        self.send_response("403 Forbidden", [("Content-Length", 0,)])
        self.delete_this_no_sent_data()

    def get_kv_value(self, kv_pairs, name):
        for k, v in kv_pairs:
            if name.lower() == k.lower():
                return v
            ''''''
        return None

    def handle_tcp_data(self, info):
        _id, win_size, tcp_data = info
        if _id not in self.__packet_id_map: return

        fd = self.__packet_id_map[_id]

        if self.handler_exists(fd):
            self.__win_size = win_size
            self.send_message_to_handler(self.fileno, fd, tcp_data)

    def handle_udp_udplite_data(self, info):
        _id, address, port, addr_type, byte_data = info
        if _id not in self.__packet_id_map: return
        fd = self.__packet_id_map[_id]
        if self.handler_exists(fd):
            self.dispatcher.get_handler(fd).msg_send((address, port,), byte_data)

    def handle_udp_udplite_conn(self, info, is_udplite=False):
        _id, address, port, addr_type, byte_data = info
        # ID存在直接返回一个建立失败
        if _id in self.__packet_id_map:
            self.send_conn_state(_id, 1)
            return

        is_ipv6 = False
        if addr_type in (socks2https.ADDR_TYPE_FORCE_DOMAIN_IPv6, socks2https.ADDR_TYPE_IPv6,): is_ipv6 = True

        fd = self.create_handler(self.fileno, handler_for_udp, (address, port), _id, is_udplite=is_udplite,
                                 is_ipv6=is_ipv6)
        if fd < 0:
            self.send_conn_state(_id, 1)
            return
        self.__packet_id_map[_id] = fd

    def handle_tcp_conn_request(self, info):
        _id, address, port, addr_type, byte_data = info
        # 如果包ID存在那么发送错误
        if _id in self.__packet_id_map:
            self.send_conn_state(_id, 1)
            return

        is_ipv6 = False
        if addr_type in (socks2https.ADDR_TYPE_FORCE_DOMAIN_IPv6, socks2https.ADDR_TYPE_IPv6,): is_ipv6 = True

        fd = self.create_handler(self.fileno, handler_for_tcp, (address, port), _id, is_ipv6=is_ipv6)

        if fd < 0:
            self.send_conn_state(_id, 1)
            return
        self.__packet_id_map[_id] = fd

    def send_conn_state(self, _id, err_code):
        data = self.__builder.build_conn_state(_id, err_code)
        self.send_data(data)

    def handle_tcp_conn_state(self, info):
        _id, err_code = info
        if err_code != 0:
            if _id in self.__packet_id_map: del self.__packet_id_map[_id]
        return

    def handle_request_data(self):
        self.__parser.input(self.reader.read())
        self.__time = time.time()

        while 1:
            try:
                self.__parser.parse()
            except socks2https.FrameError:
                self.delete_handler(self.fileno)
                return
            rs = self.__parser.get_result()
            if not rs: break
            frame_type, info = rs

            if frame_type == socks2https.FRAME_TYPE_PING:
                self.send_pong()
                continue
            if frame_type == socks2https.FRAME_TYPE_PONG:
                self.handle_pong()
                continue
            if frame_type == socks2https.FRAME_TYPE_TCP_CONN:
                self.handle_tcp_conn_request(info)
                continue
            if frame_type == socks2https.FRAME_TYPE_UDP_CONN:
                continue
            if frame_type == socks2https.FRAME_TYPE_UDPLite_CONN:
                continue
            if frame_type == socks2https.FRAME_TYPE_CONN_STATE:
                self.handle_tcp_conn_state(info)
                continue
            if frame_type == socks2https.FRAME_TYPE_TCP_DATA:
                self.handle_tcp_data(info)
                continue
            if frame_type == socks2https.FRAME_TYPE_UDP_DATA:
                self.handle_udp_udplite_data(info)
                continue
            if frame_type == socks2https.FRAME_TYPE_UDPLITE_DATA:
                self.handle_udp_udplite_data(info)
                continue
            ''''''
        return

    def send_data(self, byte_data):
        self.add_evt_write(self.fileno)
        self.writer.write(byte_data)

    def rand_bytes(self):
        n = random.randint(0, 128)
        return os.urandom(n)

    def send_pong(self):
        data = self.rand_bytes()
        pong_data = self.__builder.build_pong(data)

        self.send_data(pong_data)

    def handle_pong(self):
        self.__time = time.time()

    def send_ping(self):
        data = self.rand_bytes()
        ping_data = self.__builder.build_ping(data)

        self.send_data(ping_data)

    def tcp_readable(self):
        if not self.__handshake_ok:
            self.do_handshake()
            return
        self.handle_request_data()

    def tcp_writable(self):
        if self.writer.is_empty() and not self.__qos.have_data():
            self.remove_evt_write(self.fileno)
        while 1:
            pkts = self.__qos.get_data()
            if not pkts: break
            for pkt in pkts: self.writer.write(pkt)

    def tcp_error(self):
        self.delete_handler(self.fileno)

    def tcp_timeout(self):
        t = time.time()
        if t - self.__time > self.dispatcher.conn_timeout:
            self.delete_handler(self.fileno)
            return

        if t - self.__time > self.dispatcher.heartbeat_timeout:
            self.send_ping()
            return

        self.set_timeout(self.fileno, 10)

    def tcp_delete(self):
        for packet_id, fd in self.__packet_id_map.items():
            self.delete_handler(fd)
        self.unregister(self.fileno)
        self.close()

    def auth_user(self, uri):
        """验证用户是否合法
        :param uri:
        :return:
        """
        users_info = self.dispatcher.get_users()
        p = uri.find("?")
        if p < 1: return False

        p += 1
        s = uri[p:]
        result = urllib.parse.parse_qs(s)

        users = result.get("user", [])
        passwds = result.get("passwd", [])

        if not users or not passwds: return False

        user = users.pop(0)
        passwd = passwds.pop(0)

        rs = False

        for user_info in users_info:
            u = user_info.get("username", None)
            p = user_info.get("password", None)
            if u != user and p != passwd: continue
            rs = True

        return rs

    def tell_close(self, packet_id):
        fd = self.__packet_id_map[packet_id]
        self.send_conn_state(packet_id, 1)
        self.delete_handler(fd)

        del self.__packet_id_map[packet_id]

    def send_tcp_data(self, packet_id, data):
        if packet_id not in self.__packet_id_map: return
        if not data: return

        while 1:
            if not data: break
            frag_data = data[0:self.__win_size]
            wrap_data = self.__builder.build_tcp_frame_data(packet_id, frag_data, win_size=self.__my_win_size)
            self.__qos.input(packet_id, wrap_data)
            data = data[self.__win_size:]

        self.add_evt_write(self.fileno)

    def send_udp_udplite_data(self, packet_id, ip_addr, port, addr_type, byte_data):
        if addr_type not in socks2https.addr_types: return
        if packet_id not in self.__packet_id_map: return

        # UDP和UDPLite数据包立刻发送
        data = self.__builder.build_conn_frame(packet_id, addr_type, ip_addr, port, byte_data=byte_data)
        self.send_data(data)

    def tell_conn_ok(self, packet_id):
        data = self.__builder.build_conn_state(packet_id, 0)
        self.send_data(data)


class handler_for_tcp(tcp_handler.tcp_handler):
    __creator = None
    __packet_id = None
    __time = None

    def init_func(self, creator_fd, address, packet_id, is_ipv6=False):
        self.__creator = creator_fd
        self.__packet_id = packet_id
        self.__time = time.time()

        if is_ipv6:
            fa = socket.AF_INET6
        else:
            fa = socket.AF_INET
        s = socket.socket(fa, socket.SOCK_STREAM)
        self.set_socket(s)

        self.connect(address)

        return self.fileno

    def connect_ok(self):
        self.register(self.fileno)
        self.add_evt_read(self.fileno)
        self.dispatcher.get_handler(self.__creator).tell_conn_ok(self.__packet_id)

    def tcp_readable(self):
        if not self.handler_exists(self.__creator): return
        self.__time = time.time()
        rdata = self.reader.read()
        self.dispatcher.get_handler(self.__creator).send_tcp_data(self.__packet_id, rdata)

    def tcp_writable(self):
        if self.writer.is_empty(): self.remove_evt_write(self.fileno)

    def tcp_error(self):
        self.dispatcher.get_handler(self.__creator).tell_close(self.__packet_id)

    def tcp_timeout(self):
        if not self.is_conn_ok():
            self.dispatcher.get_handler(self.__creator).tell_close(self.__packet_id)
            return
        t = time.time()
        if t - self.__time > self.dispatcher.conn_timeout:
            self.dispatcher.get_handler(self.__creator).tell_close(self.__packet_id)
            return
        self.set_timeout(self.fileno, 10)

    def tcp_delete(self):
        self.unregister(self.fileno)
        self.close()

    def message_from_handler(self, from_fd, byte_data):
        if not self.is_conn_ok(): return
        self.writer.write(byte_data)
        self.add_evt_write(self.fileno)
        self.__time = time.time()


class handler_for_udp(udp_handler.udp_handler):
    __creator = None
    __packet_id = None
    __time = None
    __access = None
    __timer = None
    __addr_type = None

    def init_func(self, creator_fd, address, packet_id, is_udplite=False, is_ipv6=False):
        self.__creator = creator_fd
        self.__packet_id = packet_id
        self.__time = time.time()
        self.__access = {}
        self.__timer = timer.timer()

        if not self.dispatcher.enable_ipv6 and is_ipv6: return -1

        if is_ipv6:
            fa = socket.AF_INET6
            listen_ip = self.dispatcher.listen_ipv6
            self.__addr_type = socks2https.ADDR_TYPE_IPv6
        else:
            self.__addr_type = socks2https.ADDR_TYPE_IP
            fa = socket.AF_INET
            listen_ip = self.dispatcher.listen_ip
        s = socket.socket(fa, socket.SOCK_DGRAM)
        self.set_socket(s)
        self.bind((listen_ip, 0))

        self.register(self.fileno)
        self.add_evt_read(self.fileno)

        return self.fileno

    def udp_readable(self, message, address):
        # 检查UDP数据包是否合法
        if address[0] not in self.__access: return
        if not self.handler_exists(self.__creator): return

        self.dispatcher.send_udp_udplite_data(self.__packet_id, address[0], address[1], self.__addr_type, message)

    def udp_writable(self):
        self.remove_evt_write(self.fileno)

    def udp_delete(self):
        self.unregister(self.fileno)
        self.close()

    def udp_error(self):
        self.dispatcher.tell_close(self.__packet_id)

    def udp_timeout(self):
        t = time.time()
        if t - self.__time > self.dispatcher.conn_timeout:
            self.dispatcher.tell_close(self.__packet_id)
            return

        names = self.__timer.get_timeout_names()
        for name in names:
            if name in self.__access:
                self.__timer.drop(name)
                del self.__access[name]
            ''''''

        self.set_timeout(self.fileno, 10)

    def msg_send(self, address, byte_data):
        self.__time = time.time()

        if address[0] not in self.__access:
            self.__access[address[0]] = None
            self.__timer.set_timeout(address[0], 60)
        self.sendto(byte_data, address)
        self.add_evt_write(self.fileno)