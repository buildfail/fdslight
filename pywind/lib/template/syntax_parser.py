#!/usr/bin/env python3
"""核心语法解析器,实现核心语法解析功能
"""


class SyntaxErr(Exception): pass


class ParserErr(Exception): pass


class parser(object):
    def __parse_block_tag_property(self, tag_content, property_name):
        """获取块标签的属性值
        :param tag_content:块标签内容 
        :param property_name: 属性名
        :return: 
        """
        pass

    def parse_single_syntax(self, s):
        """解析美元符号
        :param sts: 
        :return: 
        """
        results = []

        while 1:
            pos = sts.find("${")
            if pos < 0:
                results.append((False, sts,))
                break
            s1 = sts[0:pos]
            if s1: results.append((False, s1,))
            pos += 2
            sts = sts[pos:]
            pos = sts.find("}")
            if pos < 1: raise SyntaxErr
            s2 = sts[0:pos]
            results.append((True, s2,))
            pos += 1
            sts = sts[pos:]

        return results

    def parse_pycode_block(self, sts):
        results = []

        start = "<%"
        end = "%>"

        size_begin = 2
        size_end = 2

        while 1:
            pos = sts.find(start)
            if pos < 0:
                results.append((False, sts,))
                break
            s1 = sts[0:pos]
            if s1: results.append((False, s1,))

            pos += size_begin
            sts_bak = sts
            sts = sts[pos:]

            pos = sts.find(end)
            if pos < 0:
                results.append((False, sts_bak,))
                break

            results.append((True, sts[0:pos]))
            pos += size_end
            sts = sts[pos:]

        return results

    def parse_tpl_block(self, sts):
        """解析模版块
        :param sts: 
        :return: 
        """
        results = []

        while 1:
            pos = sts.find("<%block")
            if pos < 0:
                results.append((False, sts))
                break

            t_sts = sts[pos:]
            t = t_sts.find(">")
            t += pos

            if t < 1:
                results.append((False, sts))
                break
            tt = t - 1
            t = t + 1

            s1 = sts[0:pos]
            s2 = sts[pos:t]

            if sts[tt] == "/":
                results.append((False, s1))
                results.append((True, (s2, "",),))
                sts = sts[t:]
                continue
            pos = sts.find("</%block>")

            if pos < 9:
                results.append((False, sts))
                break
            s3 = sts[t:pos]
            pos += 9
            results.append((False, s1,))
            results.append((True, (s2, s3,),))
            sts = sts[pos:]

        return results
