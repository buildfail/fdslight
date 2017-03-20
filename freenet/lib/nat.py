#!/usr/bin/env python3
import freenet.lib.checksum as checksum
import freenet.lib.ipaddr as ipaddr
import pywind.lib.timer as timer
import socket


class _nat_base(object):
    # sesison ID到服务端虚拟出来的局域网的映射
    __sessionId2sLan = None
    # 服务端虚拟出来的局域网到客户端的局域网IP的映射
    __sLan2cLan = None

    def __init__(self):
        self.__sessionId2sLan = {}
        self.__sLan2cLan = {}

    def add2Lan(self, session_id, clan_addr, slan_addr):
        if session_id not in self.__sessionId2sLan: self.__sessionId2sLan[session_id] = {}
        t = self.__sessionId2sLan[session_id]
        t[clan_addr] = slan_addr

        if slan_addr not in self.__sLan2cLan: self.__sLan2cLan[slan_addr] = {}
        t = self.__sLan2cLan[slan_addr]
        t["session_id"] = session_id
        t["clan_addr"] = clan_addr

    def delLan(self, slan_addr):
        if slan_addr not in self.__sLan2cLan: return

        ta = self.__sLan2cLan[slan_addr]
        clan_addr = ta["clan_addr"]
        session_id = ta["session_id"]

        del self.__sLan2cLan[slan_addr]

        tb = self.__sessionId2sLan[session_id]
        del tb[clan_addr]
        if not tb: del self.__sessionId2sLan[session_id]

    def find_sLanAddr_by_cLanAddr(self, session_id, clan_addr):
        """根据客户端局域网中的IP找到服务端对应的局域网IP"""
        if session_id not in self.__sessionId2sLan: return None
        t = self.__sessionId2sLan[session_id]
        if clan_addr not in t: return None
        return t[clan_addr]

    def find_cLanAddr_by_sLanAddr(self, slan_addr):
        """根据服务端的虚拟局域网IP找到客户端对应的局域网IP"""
        if slan_addr not in self.__sLan2cLan: return None
        t = self.__sLan2cLan[slan_addr]

        return t

    def get_ippkt2sLan_from_cLan(self, session_id, ippkt):
        """重写这个方法
        把客户端局域网中的数据包转换成服务器虚拟局域网的包
        """
        return b""

    def get_ippkt2cLan_from_sLan(self, session_id, ippkt):
        """重写这个方法
        把服务端虚拟局域网中的包转换为客户端局域网中的数据包
        """
        return (bytes(16), b"",)

    def recycle(self):
        """回收资源,重写这个方法"""
        pass


class nat(_nat_base):
    __ip_alloc = None
    __timer = None
    # 映射IP的有效时间
    __VALID_TIME = 900

    def __init__(self, subnet):
        super(nat, self).__init__()
        self.__ip_alloc = ipaddr.ipalloc(*subnet, is_ipv6=False)
        self.__timer = timer.timer()

    def get_ippkt2sLan_from_cLan(self, session_id, ippkt):
        clan_saddr = ippkt[12:16]
        slan_saddr = self.find_sLanAddr_by_cLanAddr(session_id, clan_saddr)

        if not slan_saddr:
            slan_saddr = self.__ip_alloc.get_addr()
            self.add2Lan(session_id, clan_saddr, slan_saddr)

        data_list = list(ippkt)
        checksum.modify_address(slan_saddr, data_list, checksum.FLAG_MODIFY_SRC_IP)
        self.__timer.set_timeout(slan_saddr, self.__VALID_TIME)

        return bytes(data_list)

    def get_ippkt2cLan_from_sLan(self, ippkt):
        slan_daddr = ippkt[16:20]
        rs = self.find_cLanAddr_by_sLanAddr(slan_daddr)

        if not rs: return None

        data_list = list(ippkt)
        checksum.modify_address(rs["clan_addr"], data_list, checksum.FLAG_MODIFY_DST_IP)
        self.__timer.set_timeout(slan_daddr, self.__VALID_TIME)

        return (rs["session_id"], bytes(data_list),)

    def recycle(self):
        names = self.__timer.get_timeout_names()
        for name in names:
            if self.__timer.exists(name): self.__timer.drop(name)
            self.delLan(name)
            self.__ip_alloc.put_addr(name)
        return


class nat66(object):
    __byte_local_ip6 = None
    __timer = None

    __nat = None
    __nat_reverse = None

    # NAT超时时间
    __NAT_TIMEOUT = 900

    def __init__(self, local_ip6, nat_sessions=5000):
        """
        :param local_ip6: 本机IPv6地址
        """
        self.__timer = timer.timer()
        self.__byte_local_ip6 = socket.inet_pton(socket.AF_INET6, local_ip6)
        self.__nat = {}
        self.__nat_reverse = {}

    def __get_nat_id(self, mbuf, is_req=True):
        mbuf.offset = 8
        saddr = mbuf.get_part(16)

        mbuf.offset = 24
        daddr = mbuf.get_part(16)

        mbuf.offset = 6
        nexthdr = mbuf.get_part(1)

        mbuf.offset = 40

        if is_req:
            addr = saddr
        else:
            addr = daddr

        if nexthdr in (socket.IPPROTO_UDP, socket.IPPROTO_TCP):
            if not is_req: mbuf.offset += 2
            port = mbuf.get_part(2)
            return (
                b"".join((addr, chr(nexthdr).encode("iso-8859-1"), port,)),
                port,
            )

        # ICMPV6
        mbuf.offset = 44
        icmp_id = mbuf.get_part(2)
        return (
            b"".join((addr, chr(nexthdr).encode("iso-8859-1"), icmp_id,)),
            icmp_id
        )

    def __is_support_nat(self, mbuf, is_req=True):
        """检查IP数据包是否支持NAT
        :param mbuf:
        :param is_req:是否是请求数据包
        :return:
        """
        mbuf.offset = 6
        nexthdr = mbuf.get_part(1)

        if nexthdr in (socket.IPPROTO_TCP, socket.IPPROTO_UDP): return True
        if nexthdr != socket.IPPROTO_ICMPV6: return False

        # 检查ICMPv6类型是否支持NAT
        mbuf.offset = 40
        icmptype = mbuf.get_part(1)
        # 检查是否是echo请求
        if icmptype not in (128, 129): return False
        if icmptype == 128 and not is_req: return False
        if icmptype == 129 and is_req: return False

        return True

    def __modify_ippkt(self, mbuf, new_address, ushort, is_dest=False):
        """ 修改IP数据包
        :param new_address: 新的地址
        :param ushort: 新的unsigned short int
        :param is_dest:是修改目的信息还是修改源信息,True表示修改目的信息
        :return:
        """
        pass

    def __get_nat_session(self):
        return 0

    def __put_nat_session(self, session_id):
        pass

    def get_nat(self, session_id, mbuf):
        if not self.__is_support_nat(mbuf, is_req=True): return False

        if session_id not in self.__nat:
            self.__nat[session_id] = {}

        pydict = self.__nat[session_id]

        nat_id, old_ushort = self.__get_nat_id(mbuf)

        mbuf.offset = 8
        saddr = mbuf.get_part(16)

        mbuf.offset = 24
        daddr = mbuf.get_part(16)

        mbuf.offset = 6
        nexthdr = mbuf.get_part(1)

        if nat_id in pydict:
            ushort = pydict[nat_id]
        else:
            ushort = self.__get_nat_session()
            pydict[nat_id] = ushort

        t = (nexthdr << 16) | ushort

        if t not in self.__nat_reverse:
            self.__nat_reverse[t] = (session_id, nat_id, saddr, old_ushort, {daddr: None})
        else:
            self.__nat_reverse[t][4][daddr] = None

        self.__modify_ippkt(mbuf, self.__byte_local_ip6, ushort, is_dest=False)
        self.__timer.set_timeout(t, self.__NAT_TIMEOUT)

        return True

    def get_nat_reverse(self, mbuf):
        if self.__is_support_nat(mbuf, is_req=False): return False
        nat_id = self.__get_nat_id(mbuf, is_req=False)
        if nat_id not in self.__nat_reverse: return False

    def __del_nat(self, nat_session_id):
        if nat_session_id not in self.__nat_reverse: return
        session_id, nat_id, _, _, _ = self.__nat_reverse[nat_session_id]
        pydict = self.__nat[session_id]

        del pydict[nat_id]
        if not pydict: del self.__nat[session_id]
        self.__timer.drop(nat_session_id)

    def recycle(self):
        for name in self.__timer.get_timeout_names():
            if not self.__timer.exists(name): continue
            self.__del_nat(name)
        return