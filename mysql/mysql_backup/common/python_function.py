#!/usr/bin/env python
#coding=utf8

# version 2.0:xtrbackup不压缩 | arthur | 2019-02-25

import sys
import os
import os.path
import time
import re
import pprint
import datetime
import commands
import subprocess
import logging
import linecache
import MySQLdb
import MySQLdb.cursors
import shutil


class MySQLBackupFunction(object):

    def __init__(self, BF=None, dconf=None):

        self.BF = BF
        self.dconf = dconf

    def getOnlineFullbackupInfo(self):
    
        sql=("""select  
                        Ftype as instance,
                        Faddress as address,
                        Fsource_host as source_host,
                        Fsource_port as source_port,
                        Fxtrabackup_state as xtrabackup_state,
                        Fxtrabackup_weekday as xtrabackup_weekday,
                        Fxtrabackup_start_time as xtrabackup_start_time,
                        Fxtrabackup_end_time as xtrabackup_end_time,
                        Fxtrabackup_clear_rule as xtrabackup_clear_rule,
                        Fmydumper_state as mydumper_state,
                        Fmydumper_weekday as mydumper_weekday,
                        Fmydumper_start_time as mydumper_start_time,
                        Fmydumper_end_time as mydumper_end_time,
                        Fmydumper_clear_rule as mydumper_clear_rule,
                        Fmysqldump_state as mysqldump_state,
                        Fmysqldump_weekday as mysqldump_weekday,
                        Fmysqldump_start_time as mysqldump_start_time,
                        Fmysqldump_end_time as mysqldump_end_time,
                        Fmysqldump_clear_rule as mysqldump_clear_rule
                from %s where Fstate='online' and Faddress='%s'"""%
                (self.dconf['t_mysql_backup_info'],self.dconf['local_ip']))
        v = self.BF.connMySQL(sql, self.dconf['conn_dbadb'])
        return v


    def getOnlineBinarybackupInfo(self):
    
        sql = ("""select Ftype as instance,
                        Fsource_host as source_host,
                        Fsource_port as source_port,
                        Fbinary_name as name
                    from {table}
                    where Fstate='online'
                        and Faddress='{address}'"""
                    .format(table = self.dconf['t_mysql_backup_info'],
                        address = self.dconf['local_ip']
                    )
               )
        v = self.BF.connMySQL(sql, self.dconf['conn_dbadb'])
        return v


    def checkSlave(self, d=None):

        slave_status = self.BF.connMySQL(sql="show slave status;", d=d)
        if not slave_status: # Master
            role = "master"
            delay_time = 0
        else: # Master or Slave
            if (slave_status[0]['Slave_IO_Running'] == "Yes" 
                and slave_status[0]['Slave_SQL_Running'] == "Yes"): # Slave
                role = "slave"
                delay_time = slave_status[0]['Seconds_Behind_Master']
            else:
                role = "master"
                delay_time = 0
        return role, delay_time
            

    def doProtect(self):
        
        max_delay_time = 120 # 延迟阈值
        max_ddl_cnt = 10 # ddl线程数阈值

        d = {'host': self.dconf['f_info']['source_host'], 
             'port': self.dconf['f_info']['source_port'],
             'user': self.dconf['admin_user'],
             'passwd': self.dconf['admin_pass']}
        # Waiting for global read lock
        sql = ("""select ID as id,
                         USER as user,
                         HOST as host,
                         STATE as state,
                         INFO as info 
                  from information_schema.processlist 
                  where USER='{dump_user}' and HOST like '{address}%';"""
                  #where USER='{dump_user}' and STATE like '%Waiting%lock%' and HOST like '{address}%';"""
                  .format(dump_user = self.dconf['dump_user'],
                          address = self.dconf['f_info']['address']))
        ddl_infos = self.BF.connMySQL(sql, d)

        sql = ("""select count(*) as cnt
              from information_schema.processlist where STATE like '%Waiting%lock%';""")
        cnt = self.BF.connMySQL(sql=sql, d=d)[0]['cnt']
            
        role, delay_time = self.checkSlave(d=d)
        self.BF.printLog("[%s:%s]当前机器角色为%s,延迟时间为%ss,延迟阈值为%ss,mdl锁总数为%s,mdl线程阈值为%s"%
                          (self.dconf['f_info']['source_host'], 
                                self.dconf['f_info']['source_port'],
                                role, delay_time, max_delay_time, cnt, max_ddl_cnt), 
                           self.dconf['normal_log'], 'red')

        if cnt > 0:
            if ((role == "master" and cnt >= max_ddl_cnt) 
                    or (role == "slave" and cnt >= max_ddl_cnt) 
                    or (role == "slave" and delay_time >= max_delay_time)):
                for ddl_info in ddl_infos:
                    self.BF.printLog("[%s:%s]开始进入kill逻辑,kill %s(user:%s,host:%s,state:%s,info:%s)"
                            %(self.dconf['f_info']['source_host'],
                                self.dconf['f_info']['source_port'],
                                ddl_info['id'],
                                ddl_info['user'],
                                ddl_info['host'],
                                ddl_info['state'],
                                ddl_info['info']),
                            self.dconf['normal_log'], 'red')
                    # BUGS:没有更新备份结果表
                    self.BF.connMySQL(sql="kill %s;"%(ddl_info['id']), d=d)

    def rmANDUpdateBackuppath(self, backup_path=None, task_id=None):

        self.BF.printLog("[%s]清理数据:%s"%(task_id, backup_path),
                                            self.dconf['normal_log'], 'green')
        #shutil.rmtree(backup_path) 
        self.BF.runShell("rm -rfv %s"%backup_path)
        u_sql = ("""update {t_mysql_backup_result} 
                    set Fclear_status='done',Fmodify_time=now() 
                    where Ftask_id='{task_id}';"""
                .format(t_mysql_backup_result = self.dconf['t_mysql_backup_result'],
                        task_id = task_id))
        self.BF.connMySQL(u_sql, self.dconf['conn_dbadb'])


    def dealBackupFail(self, instance=None):
        sql = ("""select Ftask_id as task_id,
                         Fpath as backup_path 
                  from %s 
                  where Ftype='%s' 
                        and Fbackup_status='Fail' 
                        and Fclear_status!='done';"""
                %(self.dconf['t_mysql_backup_result'], instance))
        infos = self.BF.connMySQL(sql, self.dconf['conn_dbadb'])
        for info in infos:
            task_id = info['task_id']
            backup_path = info['backup_path']
            self.rmANDUpdateBackuppath(backup_path=backup_path, task_id=task_id)
        

    def addToList(self, l=None, string=None):

        if string not in l:
            l.append(string)
        return l


    def clearData(self, backup_mode=None, clear_rule=None, instance=None):
        
        """
        待保留的数据:
        最老的一份数据
        最新的一份数据
        在区间内最新的一份数据
        """
        b_sql = ("""select Ftask_id as task_id
                  from %s
                  where Ftype='%s' 
                        and Fbackup_status='Succ' 
                        and Fclear_status='todo' 
                        and Fremote_backup_status='todo'
                        and Fmode='%s'"""
                 %(self.dconf['t_mysql_backup_result'], instance, backup_mode))

        t = [] #Tips:待保存的task_id列表

        # Tips:以下为需要保留的数据集
        max_old = self.BF.connMySQL("%s order by Fdate limit 1;"%b_sql, self.dconf['conn_dbadb']) # 最老的一份数据
        max_new = self.BF.connMySQL("%s order by Fdate desc limit 1;"%b_sql, self.dconf['conn_dbadb']) # 最新的一份数据
        if max_old:
            max_old_task_id = "'%s'"%max_old[0]['task_id']
            t = self.addToList(l=t, string=max_old_task_id)
        if max_new:
            max_new_task_id = "'%s'"%max_new[0]['task_id']
            t = self.addToList(l=t, string=max_new_task_id)
        # 计算区间内最新的一份数据
        if clear_rule:
            l = clear_rule.split('-',-1)
            l_len = len(l)
            l.append('36600') # Tips:1、写死最大值为100 year;2、l的长度有增长,并不能在重复计算len
            for i in range(l_len):
                first_num = int("-%s"%l[i])
                last_num = int("-%s"%l[i+1])
                now_date = datetime.datetime.now() 
                f_date =  (now_date + datetime.timedelta(days=first_num)).strftime("%Y-%m-%d") 
                l_date =  (now_date + datetime.timedelta(days=last_num)).strftime("%Y-%m-%d") 
                #print ("""%s and Fdate>='%s' and Fdate<'%s' 
                #                           order by Fdate desc limit 1;"""%(b_sql,l_date,f_date))
                cur = self.BF.connMySQL("""%s and Fdate>='%s' and Fdate<'%s' 
                                           order by Fdate desc limit 1;"""
                                        %(b_sql,l_date,f_date), self.dconf['conn_dbadb'])
                if cur:
                    task_id = "'%s'"%cur[0]['task_id']
                    t = self.addToList(l=t, string=task_id)
                    self.BF.printLog("""[%s]本次区间范围为(%s)天-(%s)天,时间周期为(%s)-(%s),待保存的task_id为:%s"""
                                    %(instance,first_num,last_num,f_date,l_date,task_id), 
                                        self.dconf['normal_log'], 'green')
                else:
                    self.BF.printLog("""[%s]本次区间范围为(%s)天-(%s)天,时间周期为(%s)-(%s),找不到待保存的task_id"""
                                        %(instance,first_num,last_num,f_date,l_date),
                                           self.dconf['normal_log'], 'green')
        w = '' # 初始化where条件
        for i in t:
            w = w + ",%s"%(i) if w else i # 三目操作
        sql = ("""select Ftask_id as task_id,
                         Fpath as backup_path
                  from {t}
                  where Ftype='{instance}' 
                        and Fbackup_status='Succ' 
                        and Fclear_status='todo' 
                        and Fremote_backup_status='todo'
                        and Fmode='{mode}'
                        and Ftask_id not in ({w})"""
                .format(t=self.dconf['t_mysql_backup_result'],
                        instance = instance,
                        mode = backup_mode,
                        w = w))
        d_infos = self.BF.connMySQL(sql, self.dconf['conn_dbadb']) # 最新的一份数据
        if not d_infos:
            #self.BF.printLog("""[%s-%s]找不到待清理的task_id"""%(instance,backup_mode), self.dconf['normal_log'], 'red')
            pass
            
        for d_info in d_infos:
            task_id = d_info['task_id']
            backup_path = d_info['backup_path']
            self.rmANDUpdateBackuppath(backup_path=backup_path, task_id=task_id)

    def dealBackupSucc(self, instance=None):

        sql2 = ("""select Fxtrabackup_clear_rule as xtrabackup_clear_rule,
                          Fmydumper_clear_rule as mydumper_clear_rule,
                          Fmysqldump_clear_rule as mysqldump_clear_rule 
                    from {t_mysql_backup_info} 
                    where Ftype='{instance}' 
                          and Faddress='{backup_machine}';"""
                    .format(t_mysql_backup_info = self.dconf['t_mysql_backup_info'],
                            instance = instance,
                            backup_machine = self.dconf['local_ip']))
        clear_rule = self.BF.connMySQL(sql2, self.dconf['conn_dbadb'])

        try:
            mysqldump_clear_rule = clear_rule[0]['mysqldump_clear_rule']
            xtrabackup_clear_rule = clear_rule[0]['xtrabackup_clear_rule']
            mydumper_clear_rule = clear_rule[0]['mydumper_clear_rule']
        except Exception,e: # 清理规则不一定存在
            mysqldump_clear_rule = None
            xtrabackup_clear_rule = None
            mydumper_clear_rule = None

        self.clearData(backup_mode='xtrabackup', clear_rule=xtrabackup_clear_rule, instance=instance)
        self.clearData(backup_mode='mydumper', clear_rule=mydumper_clear_rule, instance=instance)
        self.clearData(backup_mode='mysqldump', clear_rule=mysqldump_clear_rule, instance=instance)

    # Tips:递归调用自己
    def doClear(self):
        sql = """select Ftype as instance from %s"""%(self.dconf['t_mysql_backup_info'])
        instances = self.BF.connMySQL(sql, self.dconf['conn_dbadb'])
        for instance in instances:
            instance = instance['instance']
            """
            Tips
            1、备份失败了的，但是还没清理
                where Ftype='DBADB' and Fbackup_status='Fail' and Fclear_status!='done';
            2、备份成功了的，并且是待清理状态，并且远程备份状态为上传成功
                where Fbackup_status='Succ' and Fclear_status='todo' and Fremote_backup_status='done';
            """
            self.dealBackupFail(instance=instance)
            self.dealBackupSucc(instance=instance)
                

    def getDiskPercent(self, mount=None):

        cmd = "df -hP %s | tail -1| awk '{print $5}'| sed 's/%%//g'"%(mount)
        p = self.BF.runShell(cmd)[1]
        if p:
            try:
                p = int(p)
            except Exception,e:
                p = False
        else:
            p = False
        return p

    def findFirstBinaryLogName(self):

        sql = "SHOW BINARY LOGS;"
        names = self.BF.connMySQL(sql=sql, d=self.dconf['conn_instance'], is_dict=0)
        if names:
            name = names[0][0]
        else:
            name = False
        return name

    def checkFullbackupTaskID(self, task_id=None):
        """return backup_status for task_id"""
        sql = ("""select Fbackup_status as backup_status from %s where Ftask_id='%s';"""
                %(self.dconf['t_mysql_backup_result'], task_id))
        v = self.BF.connMySQL(sql, self.dconf['conn_dbadb'])
        return v

    def getToday(self):
        """获取今天date,比如1970-01-01"""
        return time.strftime('%F',time.localtime())
    
    def compactTime(self, t):
        """获取今天date,比如19700101"""
        return t.replace('-', '')
    
    def getWeekDay(self):
        """获取本周是周几,比如返回0,代表周日"""
        return datetime.datetime.now().strftime("%w")
    
    def getSpecialtoday(self, w):
        """获取本周具体周几的日期"""
        # 本周周日的日期
        weekday_sun = (datetime.date.today() - 
                       datetime.timedelta(days=datetime.date.today().weekday()) 
                        + datetime.timedelta(days=6)) 
        t = weekday_sun + datetime.timedelta(days=int(w))
        return t
    
    def getFullbackupTaskID(self, backup_mode=None):
        today = self.getToday()
        compact_today = self.compactTime(today)
        weekday = int(self.getWeekDay())
        backup_mode = backup_mode.lower()
        if backup_mode == 'xtrabackup':
            backup_weekday = self.dconf['f_info']['xtrabackup_weekday']
            if self.dconf['f_info']['xtrabackup_state'].lower() != 'online':
                task_id = False
                return task_id # Tips:trap
        elif backup_mode == 'mydumper':
            backup_weekday = self.dconf['f_info']['mydumper_weekday']
            if self.dconf['f_info']['mydumper_state'].lower() != 'online':
                task_id = False
                return task_id # Tips:trap
        elif backup_mode == 'mysqldump':
            backup_weekday = self.dconf['f_info']['mysqldump_weekday']
            if self.dconf['f_info']['mysqldump_state'].lower() != 'online':
                task_id = False
                return task_id # Tips:trap
        else:
            task_id = False
            return task_id # Tips:trap

        backup_weekday = int(backup_weekday)
        if backup_weekday == 9:
            task_id = "{instance}-{datetime}-{backup_mode}".format(instance=self.dconf['f_info']['instance'], 
                                                                   datetime=compact_today, 
                                                                   backup_mode=backup_mode)
        elif backup_weekday in range(0,7):
            if backup_weekday > weekday: # 非我良时
                task_id = False
                return task_id # Tips:trap
            else:
                d_today = self.getSpecialtoday(backup_weekday)
                d_compact_today = self.compactTime(str(d_today))
                task_id = "{instance}-{datetime}-{backup_mode}".format(instance=self.dconf['f_info']['instance'], 
                                                                       datetime=compact_today, 
                                                                       backup_mode=backup_mode)
        else:
            task_id = False
            return task_id # Tips:trap
        task_id = task_id.upper()
        return task_id


    def doBackup(self, backup_mode=None, backup_path=None):
        # Returns:True|False
        v_check = self.checkBackup(backup_path=backup_path, backup_mode=backup_mode)
        if not v_check:
            self.BF.printLog("[%s]不进入备份逻辑"%(backup_path), self.dconf['full_log'], 'green')
            v = False 
        else:
            self.BF.printLog("[%s]进入备份逻辑,开始备份"%(backup_path), self.dconf['full_log'], 'green')
            backup_mode = backup_mode.upper()
            if backup_mode.upper() == 'MYDUMPER':
                self.doBackupMydumper(wait=0, backup_path=backup_path)
                v = True
            elif backup_mode.upper() == 'XTRABACKUP':
                self.doBackupXtrabackup(wait=0, backup_path=backup_path)
                v = True
            elif backup_mode.upper() == 'MYSQLDUMP':
                self.doBackupMysqldump(wait=0, backup_path=backup_path)
                v = True
            else:
                v = False
        return v
        

    def checkPath(self, path=None):
        if os.path.isdir(path):
            if os.listdir(path):
                # BUGS:目录存在数据,也进入备份逻辑,并且不清理老数据
                self.BF.printLog("目录不为空:%s"%(path), self.dconf['full_log'])
                #self.BF.runShell("rm -rfv %s"%path) # 清理目录
                #v = False
                v = True
            else:
                v = True
        else:
            self.BF.printLog("找不到目录:%s"%(path), self.dconf['full_log'])
            v = False
        return v


    def checkActiveTrx(self, conn_setting=None):
    
        sql = "select count(*) as cnt from information_schema.innodb_trx;"
        cnt = self.BF.connMySQL(sql, conn_setting)
        if cnt[0]['cnt'] > 0:
            self.BF.printLog("""[%s:%s]数据库存在活跃事务:%s"""
                                %(conn_setting['host'],conn_setting['port'],cnt[0]['cnt']),
                                self.dconf['full_log'])
            v = False
        else:
            v = True
        return v


    def getBinarylogTime(self, f, mysqlbinlog):
    
        cmd = ("""%s -vv %s 2>&1 | head -100 | grep "server id " | awk -F"server id" '{print $1}' | sed 's/#//g'| head -1"""%(mysqlbinlog, f))
        time1 = self.BF.runShell(cmd)[1]
        cmd2 = """date -d "%s" +"%%F %%T" """%(time1)
        time2 = self.BF.runShell(cmd2)[1]
        time2 = time2 if time2 else "1970-01-01 00:00:00"
        return time2

    def updateBinarybackup(self, d_result=None):
        sql = ("""select count(*) as cnt from {table} 
                    where Fsource_host='{host}' and Fsource_port='{port}' 
                    and Fname='{name}'"""
                .format(table = self.dconf['t_mysql_binarylog_result'],
                    host = d_result['host'],
                    port = d_result['port'],
                    name = d_result['name'])
              )

        cnt = self.BF.connMySQL(sql, self.dconf['conn_dbadb'])
        if cnt[0]['cnt'] == 0:
            u_sql = ("""insert into {table}"""
                            """ (Ftype,Fname,Fdate,Fsource_host,Fsource_port"""
                            """,Faddress,Fpath,Fsize,Fstart_time,Fbackup_status"""
                            """,Fbackup_info,Fcreate_time,Fmodify_time)"""
                            """ values"""
                            """ ('{instance}','{name}',curdate(),'{host}','{port}'"""
                            """,'{address}','{path}','{size}','{start_time}','{backup_status}'"""
                            """,'{backup_info}',now(),now());"""
                        .format(table = self.dconf['t_mysql_binarylog_result'],
                            instance = d_result['instance'],
                            name = d_result['name'],
                            host = d_result['host'],
                            port = d_result['port'],
                            address = d_result['address'],
                            path = d_result['path'],
                            size = d_result['size'],
                            start_time = d_result['start_time'],
                            backup_status = d_result['backup_status'],
                            backup_info = d_result['backup_info']
                        )
                    )
        else:
            u_sql = ("""update {table}"""
                            """ set Ftype='{instance}',Fname='{name}',Fdate=curdate(),"""
                            """ Fsource_host='{host}',Fsource_port='{port}'"""
                            """,Faddress='{address}',Fpath='{path}',Fsize='{size}'"""
                            """,Fstart_time='{start_time}',Fbackup_status='{backup_status}'"""
                            """,Fbackup_info='{backup_info}',Fmodify_time=now()"""
                            """ where Fsource_host='{host}' and Fsource_port='{port}'"""
                            """ and Fname='{name}';"""
                        .format(table = self.dconf['t_mysql_binarylog_result'],
                            instance = d_result['instance'],
                            name = d_result['name'],
                            host = d_result['host'],
                            port = d_result['port'],
                            address = d_result['address'],
                            path = d_result['path'],
                            size = d_result['size'],
                            start_time = d_result['start_time'],
                            backup_status = d_result['backup_status'],
                            backup_info = d_result['backup_info']
                        )
                    )
        #print u_sql    
        self.BF.connMySQL(u_sql, self.BF.conn_dbadb)

    def updateFullbackup(self, d=None, task_id=None, backup_mode=None, backup_path=None):

        sql = ("""select count(*) as cnt from %s where Ftask_id='%s';"""%
                    (self.dconf['t_mysql_backup_result'], task_id))
        cnt = self.BF.connMySQL(sql, self.dconf['conn_dbadb'])
        if cnt[0]['cnt'] == 0:
            u_sql = ("""insert into {table} 
                            (Ftype,Ftask_id,Fdate,Fsource_host,Fsource_port,
                            Fmode,Faddress,Fpath,Fbackup_status,Fbackup_info,
                            Fcreate_time,Fmodify_time) 
                        values 
                            ('{instance}','{task_id}',curdate(),'{source_host}','{source_port}',
                            '{mode}','{address}','{path}','{backup_status}','{backup_info}',
                            now(),now());"""
                        .format(table = self.dconf['t_mysql_backup_result'],
                                instance = self.dconf['f_info']['instance'],
                                task_id = task_id,
                                source_host = self.dconf['f_info']['source_host'],
                                source_port = self.dconf['f_info']['source_port'],
                                mode = backup_mode,
                                address = self.dconf['local_ip'],
                                path = backup_path,
                                backup_status = d['check_status'],
                                backup_info = d['memo'],
                        )
                    )
        else:
            u_sql =  ("""update {table} 
                        set 
                            Fbackup_status='{backup_status}',
                            Fsize='{size}',
                            Fmetadata=\"{metadata}\",
                            Fstart_time='{start_time}',
                            Fend_time='{end_time}',
                            Fbackup_info='{backup_info}' 
                        where Ftask_id='{task_id}';"""
                        .format(table = self.dconf['t_mysql_backup_result'],
                                backup_status = d['check_status'],
                                size = d['size'],
                                metadata = d['metadata'],
                                start_time = d['metadata']['start_time'],
                                end_time = d['metadata']['end_time'],
                                backup_info = d['memo'],
                                task_id = task_id
                            )
                     )
        #self.BF.printLog(u_sql, self.dconf['full_log'], 'green')
        self.BF.connMySQL(u_sql, self.BF.conn_dbadb)

    def changeDTTOUnixtime(self, dt):
        time.strptime(dt, '%Y-%m-%d %H:%M:%S')
        try:
            s = time.mktime(time.strptime(dt, '%Y-%m-%d %H:%M:%S'))
        except Exception,e: # Tips:避免数据库时间格式不标准
            s = 0
        return int(s)

    def checkTime(self, backup_mode=None):

        if backup_mode.upper() == 'MYDUMPER': 
            start_time = self.dconf['f_info']['mydumper_start_time']
            end_time = self.dconf['f_info']['mydumper_end_time']
        elif backup_mode.upper() == 'MYSQLDUMP': 
            start_time = self.dconf['f_info']['mysqldump_start_time']
            end_time = self.dconf['f_info']['mysqldump_end_time']
        elif backup_mode.upper() == 'XTRABACKUP': 
            start_time = self.dconf['f_info']['xtrabackup_start_time']
            end_time = self.dconf['f_info']['xtrabackup_end_time']
        else:
            return False

        t = time.strftime('%F',time.localtime())

        cur_time = time.strftime('%F %T',time.localtime())
        start_time = "%s %s"%(t, start_time)
        end_time = "%s %s"%(t, end_time)

        cur_timestamp = self.changeDTTOUnixtime(cur_time)
        start_timestamp = self.changeDTTOUnixtime(start_time)
        end_timestamp = self.changeDTTOUnixtime(end_time)

        if cur_timestamp > start_timestamp and cur_timestamp < end_timestamp:
            self.BF.printLog("[%s]当前时间:'%s',在备份时间段:'%s'-'%s'"%(backup_mode,cur_time,start_time,end_time), 
                              self.dconf['full_log'], 'green')
            return True
        else:
            self.BF.printLog("[%s]当前时间:'%s',不在备份时间段:'%s'-'%s'"%(backup_mode,cur_time,start_time,end_time), 
                              self.dconf['full_log'], 'green')
            return False


    def checkBackup(self, backup_path=None, backup_mode=None):
        """
        check backup is continue
            backup_path必须为空
            不存在活跃事务
            检测当前备份时间点,是否能继续,比如10点不在备份时间0点-8点内
            检测是否有其他备份计划任务正在执行,比如mydumper状态为backing,则不能进入xtrabackup逻辑
        Returns:True:False
        """

        conn_instance = {
            'host' : self.dconf['f_info']['source_host'],
            'port' : self.dconf['f_info']['source_port'],
            'user' : self.dconf['dump_user'],
            'passwd' : self.dconf['dump_pass']
        }
        v1 = self.checkPath(backup_path)
        v2 = self.checkActiveTrx(conn_setting=conn_instance)
        sql = ("""select Ftask_id from {t_mysql_backup_result} 
                        where Fbackup_status='Backing' and Ftype='{instance}';"""
                  .format(t_mysql_backup_result=self.dconf['t_mysql_backup_result'],
                         instance=self.dconf['f_info']['instance']))
        v3 = self.BF.connMySQL(sql, self.BF.conn_dbadb)
        v4 = self.checkTime(backup_mode=backup_mode)
        #print v1,v2,v3,v4
        #if not v1 or not v2 or v3 or not v4:
        if not v1 or not v2 or not v4:
            v = False
        else:
            v = True
        return v


    def showSlaveStatus(self, conn_setting):
        slave_status = self.BF.connMySQL("SHOW SLAVE STATUS;", conn_setting)
        if slave_status:
            Slave_IO_Running = slave_status[0]['Slave_IO_Running']
            Slave_SQL_Running = slave_status[0]['Slave_SQL_Running']
            Seconds_Behind_Master = slave_status[0]['Seconds_Behind_Master']
            Master_Host = slave_status[0]['Master_Host']
            Master_Port = slave_status[0]['Master_Port']
            Seconds_Behind_Master = slave_status[0]['Seconds_Behind_Master']
            d = {'Seconds_Behind_Master' : Seconds_Behind_Master,
                'Slave_IO_Running' : Slave_IO_Running,
                'Slave_SQL_Running' : Slave_SQL_Running,
                'Master_Host' : Master_Host,
                'Master_Port' : Master_Port,
            }
        else:
            d = False
        return d


    def resolveXtrabackupFile(self, f_tar=None):
        """
        Description:like resolveMydumperBackupFile
        xtrabackup_info like this:
        uuid = 2123e102-2ffc-11e9-a400-000c293e177f
        name = 
        tool_name = innobackupex
        tool_command = --defaults-file=/data/mysql/10000/my.cnf --tmpdir=/tmp/xtrabackup_tmpdir_22310_20190128114400 --stream=tar --user=dump_user --password=... --host=172.16.112.13 --port=10000 --no-timestamp /tmp/xtrabackup_tmpdir_22310_20190128114400
        tool_version = 2.4.5
        ibbackup_version = 2.4.5
        server_version = 5.7.19-log
        start_time = 2019-02-14 09:59:01
        end_time = 2019-02-14 09:59:14
        lock_time = 0
        binlog_pos = filename 'binlog.000013', position '24833953'
        innodb_from_lsn = 0
        innodb_to_lsn = 9154551785
        partial = N
        incremental = N
        format = tar
        compact = N
        compressed = N
        encrypted = N
        """
        if os.path.exists(f_tar):
            cmd = ("""cd %s && tar xvf backup.tar xtrabackup_info >>%s 2>&1"""%(os.path.dirname(os.path.abspath(f_tar)),
                                                                                    self.dconf['normal_log']))
            self.BF.printLog(cmd, self.dconf['normal_log'], 'green')
            # Tips:解压操作可能会很长，并且吃机器资源
            subprocess.call(cmd, stdout=subprocess.PIPE, shell=True)
            f_xtrabackup_info = "%s/xtrabackup_info"%(os.path.dirname(os.path.abspath(f_tar)))
            if os.path.exists(f_xtrabackup_info):
                cmd1 = """ cat %s | grep start_time| awk -F"= " '{print $2}' """%(f_xtrabackup_info)
                cmd2 = """ cat %s | grep end_time| awk -F"= " '{print $2}' """%(f_xtrabackup_info)
                cmd3 = """ cat %s | grep binlog_pos| awk -F"= " '{print $2}'| awk -F"," '{print $1}'| awk -F"filename " '{print $2}'| sed "s/'//g" """%(f_xtrabackup_info)
                cmd4 = """ cat %s | grep binlog_pos| awk -F"= " '{print $2}'| awk -F"," '{print $2}'| awk -F"position " '{print $2}'| sed "s/'//g" """%(f_xtrabackup_info)
                start_time = (subprocess.Popen(cmd1, stdout=subprocess.PIPE, shell=True).stdout.read()).replace('\n','')
                end_time = (subprocess.Popen(cmd2, stdout=subprocess.PIPE, shell=True).stdout.read()).replace('\n','')
                log_file = (subprocess.Popen(cmd3, stdout=subprocess.PIPE, shell=True).stdout.read()).replace('\n','')
                log_pos = (subprocess.Popen(cmd4, stdout=subprocess.PIPE, shell=True).stdout.read()).replace('\n','')
                master_host = self.dconf['f_info']['source_host']
                master_port = self.dconf['f_info']['source_port']
            else:
                start_time = '1970-01-01 00:00:00'
                end_time = start_time
                log_file = ''
                log_pos = 0
                master_host = self.dconf['f_info']['source_host']
                master_port = self.dconf['f_info']['source_port']
        else:
            start_time = '1970-01-01 00:00:00'
            end_time = start_time
            log_file = ''
            log_pos = 0
            master_host = self.dconf['f_info']['source_host']
            master_port = self.dconf['f_info']['source_port']

        # Todo:不支持GTID
        metadata = {
            'master_host' : master_host,
            'master_port' : master_port,
            'master_log_file' : log_file,
            'master_log_pos' : log_pos,
            'master_gtid' : '',
            'start_time' : start_time,
            'end_time' : end_time,
        }
        return metadata
    

    def resolveMydumperBackupFile(self, f_metadata=None):
        """
        Returns:metadata
            metadata = {
                    'start_time' : '1970-01-01',
                    'end_time' : '1970-01-01',
                    'master_log_file' : '',
                    'master_log_pos' : '',
                    'master_gtid' : '',
                    'master_host' : '',
                    'master_port' : '',
            }
        Mydumper metadata May be like this:
            Started dump at: 2018-12-23 20:40:42
            SHOW MASTER STATUS:
                Log: binlog.000006
                Pos: 34248173
                GTID:
            
            SHOW SLAVE STATUS:
                Host: 172.16.112.12
                Log: binlog.000007
                Pos: 35718139
                GTID:
            Finished dump at: 2018-12-23 20:40:48
        """

        f = open(f_metadata, 'r')
        i = 1
        master_line = 0
        slave_line = 0
        for line in f.readlines():
            line = str(line.replace('\n', ''))
            if line == 'SHOW MASTER STATUS:':
                master_line = i
            if line == 'SHOW SLAVE STATUS:':
                slave_line = i
            if re.match('Started dump at', line):
                start_time = re.sub('Started dump at: ','',line)
            if re.match('Finished dump at: ', line):
                end_time = re.sub('Finished dump at: ','',line)
            i = i + 1
        f.close()
        
        """
        Tips:
        即使f_metadata存在show slave status,有可能slave状态不是Yes,这种情况下master是source
        slave_status = connMySQL("show slave status;",source_host,int(source_port),repl_user,repl_pass)
        """
        conn_instance = {
            'host' : self.dconf['f_info']['source_host'],
            'port' : self.dconf['f_info']['source_port'],
            'user' : self.dconf['repl_user'],
            'passwd' : self.dconf['repl_pass']
        }
        slave_status = self.showSlaveStatus(conn_setting=conn_instance)
        
        if not slave_status: # source_host role is master
            master_host = self.dconf['f_info']['source_host']
            master_port = self.dconf['f_info']['source_port']
            log_file = linecache.getline(f_metadata, master_line+1).replace('\n', '').split('Log: ', -1)[1]
            log_pos = int(linecache.getline(f_metadata, master_line+2).replace('\n', '').split('Pos: ', -1)[1])
        else:
            if slave_status['Slave_IO_Running'] == "Yes" and slave_status['Slave_SQL_Running'] == "Yes": # source_host role is slave
                master_host = slave_status['Master_Host']
                master_port= slave_status['Master_Port']
                log_file = linecache.getline(f_metadata, slave_line+2).replace('\n', '').split('Log: ', -1)[1]
                log_pos = int(linecache.getline(f_metadata, slave_line+3).replace('\n', '').split('Pos: ', -1)[1])
            else: # source_host role is master, but slave status is error
                master_host = self.dconf['f_info']['source_host']
                master_port = self.dconf['f_info']['source_port']
                log_file = linecache.getline(f_metadata, master_line+1).replace('\n', '').split('Log: ', -1)[1]
                log_pos = int(linecache.getline(f_metadata, master_line+2).replace('\n', '').split('Pos: ', -1)[1])

        # Todo:不支持GTID
        metadata = {
            'master_host' : master_host,
            'master_port' : master_port,
            'master_log_file' : log_file,
            'master_log_pos' : log_pos,
            'master_gtid' : '',
            'start_time' : start_time,
            'end_time' : end_time,
        }
        return metadata


    def checkXtrabackupCommand(self):

        mysql_host = self.dconf['f_info']['source_host']
        mysql_ssh_port = self.dconf['ssh_port']
        mysql_ssh_user = self.dconf['ssh_user']
        mysql_ssh_pass = self.getSSHPass(mysql_host, mysql_ssh_user)
        print mysql_host,mysql_ssh_pass
        if not mysql_ssh_user:
            self.BF.printLog("[%s]获取密码失败"%(self.dconf['xtrabackup_task_id']), self.dconf['normal_log'], 'green')
            return False
            
        tmpdir = "/tmp/xtrabackup_tmpdir/%s"%(self.dconf['xtrabackup_task_id'])

        cmd = ("""/usr/bin/sshpass -p {mysql_ssh_pass} /usr/bin/ssh -t -o StrictHostKeyChecking=no -p {mysql_ssh_port} {mysql_ssh_user}@{mysql_host} "echo '{mysql_ssh_pass}' | sudo -S su -c \\"ps aux| grep 'innobackupex' | grep -v grep |grep 'tmpdir={tmpdir}' | wc -l  \\" " """
              .format(mysql_ssh_pass = mysql_ssh_pass,
                      mysql_ssh_port = mysql_ssh_port,
                      mysql_ssh_user = mysql_ssh_user,
                      mysql_host = mysql_host,
                      tmpdir = tmpdir))
        self.BF.printLog("[%s]检测命令(%s)"%(self.dconf['xtrabackup_task_id'],cmd), self.dconf['normal_log'], 'green')
        v = subprocess.Popen(cmd, stdout=subprocess.PIPE, shell=True)
        v = (v.stdout.read()).replace('\n','')
        try:
            if int(v) == 0:
                return True
            else:
                return False
        except Exception,e:
            return False
        

    def doCheck(self, backup_mode=None, backup_path=None, check_info=None):
        """
        check backup is succ?
        Returns:check_info
            check_info = {
                'check_status' : '',
                'metadata' : {
                    'start_time' : '1970-01-01',
                    'end_time' : '1970-01-01',
                    'master_log_file' : '',
                    'master_log_pos' : '',
                    'master_gtid' : '',
                    'master_host' : '',
                    'master_port' : ''
                },
                'size' : '0',
                'memo' : '',
            }
        """
        #backup_mode = self.dconf['f_info']['backup_mode'].upper()
        if backup_mode.upper() == 'MYDUMPER':
            #f_metadata = "%s/metadata"%(self.dconf['backup_path'])
            f_metadata = "%s/metadata"%(backup_path)
            if os.path.isfile(f_metadata):
                check_info['metadata'] = self.resolveMydumperBackupFile(f_metadata=f_metadata)
                check_info['size'] = self.BF.runShell("du -shm %s | awk '{print $1}'"%(backup_path))[1]
                check_info['check_status'] = 'Succ'
                check_info['memo'] = '备份成功'
            else: # 可能依然还在做备份
                check_info['check_status'] = 'Backing'
                check_info['memo'] = 'not find metadata'

        elif backup_mode.upper() == 'XTRABACKUP':
            # 通过tmpdir检测命令是否存在
            # 如若不存在,则认为备份完成,开始解析xtrabackup_info
            r = self.checkXtrabackupCommand()
            if r:
                #f_tar = "%s/backup.tar.gz"%(backup_path)
                # version 2.0:xtrbackup不压缩 | arthur | 2019-02-25
                f_tar = "%s/backup.tar"%(backup_path)
                check_info['metadata'] = self.resolveXtrabackupFile(f_tar=f_tar)
                if check_info['metadata']['master_log_file'] != '':
                    check_info['size'] = self.BF.runShell("du -shm %s | awk '{print $1}'"%(backup_path))[1]
                    check_info['check_status'] = 'Succ'
                    check_info['memo'] = '备份成功'
                else:
                    check_info['size'] = 0
                    check_info['check_status'] = 'Fail'
                    check_info['memo'] = '备份失败,解析xtrabackup_info失败'
            else:
                check_info['check_status'] = 'Backing'
                check_info['memo'] = 'innobackup pid is exists'
        # Tips:使用mysqldump备份方式都为备份成功状态    
        elif backup_mode.upper() == 'MYSQLDUMP':
            check_info['size'] = 0
            check_info['check_status'] = 'Succ'
            check_info['memo'] = '备份成功'

        else:
            pass
        return check_info
   

    def doBackupMysqldump(self, backup_path=None, wait=1):
        
        sql = ("""select SCHEMA_NAME as table_schema
                  from information_schema.SCHEMATA 
                 where SCHEMA_NAME 
                    not in ('information_schema','performance_schema','sys');""")
        conn_mysql = {'host': self.dconf['f_info']['source_host'],
                      'port': self.dconf['f_info']['source_port'],
                      'passwd': self.dconf['dump_pass'],
                      'user': self.dconf['dump_user']}
        table_schemas = self.BF.connMySQL(sql, conn_mysql)
        for table_schema in table_schemas:
            table_schema = table_schema['table_schema']
            cmd = ("""{mysqldump} --default-character-set=utf8 --complete-insert """
                    """ --set-gtid-purged=OFF --lock-tables=false --add-drop-table=False"""
                    """ --user='{user}'"""
                    """ --password='{password}'"""
                    """ --host='{host}' """
                    """ --port='{port}'"""
                    """ --databases {table_schema} >{backup_path}/{table_schema}.sql"""
                    """ 2>>{log_file}"""
                    .format(
                        mysqldump = self.dconf['mysqldump'],
                        user = self.dconf['dump_user'],
                        password = self.dconf['dump_pass'],
                        host = self.dconf['f_info']['source_host'],
                        port = self.dconf['f_info']['source_port'],
                        table_schema = table_schema,
                        backup_path = backup_path,
                        log_file = self.dconf['full_log'],
                    )
            )
            self.BF.printLog("开始备份库:%s(%s)"%(table_schema,cmd),
                              self.dconf['full_log'], 'green')
            if wait == 0: # not wait
                cmd = "%s &"%cmd
                subprocess.Popen(cmd, stdout=subprocess.PIPE, shell=True)
            else:
                subprocess.call(cmd, stdout=subprocess.PIPE, shell=True)
        return True


    def doBackupMydumper(self, backup_path=None, wait=1):
        cmd = ("""{mydumper} --user='{user}'"""
                    """ --password='{password}'"""
                    """ --host='{host}' """
                    """ --port='{port}'"""
                    """ --threads='{threads}'"""
                    """ --outputdir='{outputdir}'"""
                    """ --verbose=2"""
                    """ --statement-size='{statement_size}'"""
                    """ --rows='{rows}'"""
                    """ >>{log_file} 2>&1"""
                    .format(
                        mydumper = self.dconf['mydumper'],
                        user = self.dconf['dump_user'],
                        password = self.dconf['dump_pass'],
                        host = self.dconf['f_info']['source_host'],
                        port = self.dconf['f_info']['source_port'],
                        threads = self.dconf['rconf']['dump_threads'],
                        outputdir = backup_path,
                        statement_size = self.dconf['rconf']['statement_size'],
                        rows = self.dconf['rconf']['rows'],
                        log_file = self.dconf['normal_log'],
                    )
        )
        self.BF.printLog("开始备份:%s"%(cmd), self.dconf['full_log'])
        if wait == 0: # not wait
            cmd = "%s &"%cmd
            subprocess.Popen(cmd, stdout=subprocess.PIPE, shell=True)
        else:
            subprocess.call(cmd, stdout=subprocess.PIPE, shell=True)
        return True


    # 获取服务器密码
    def getSSHPass(self, ssh_host, ssh_user):
        return 'redhat'
        # BUGS:待优化
        #sql = ("""select Fclear_pass from mysql_info_db.t_server_user_info 
        #         where Fuser_name='%s' and Fserver_host='%s';"""%(ssh_user, ssh_host))
        #ssh_pass = self.BF.connMySQL(sql, self.dconf['conn_dbadb'])
        #if ssh_pass:
        #    ssh_pass = ssh_pass[0]['Fclear_pass']
        #else:
        #    ssh_pass = False
        #return ssh_pass


    # 远程xtrbackup
    def doBackupXtrabackup(self, backup_path=None, wait=1):
       
        # Tips:写死
        mysql_host = self.dconf['f_info']['source_host']
        mysql_ssh_port = self.dconf['ssh_port'] 
        mysql_ssh_user = self.dconf['ssh_user']
        mysql_ssh_pass = self.getSSHPass(mysql_host, mysql_ssh_user)
        backup_ssh_port = self.dconf['ssh_port']
        backup_ssh_user = self.dconf['ssh_user']
        backup_ssh_pass = self.getSSHPass(self.dconf['local_ip'], backup_ssh_user)
        tmpdir = "/tmp/xtrabackup_tmpdir/%s"%(self.dconf['xtrabackup_task_id'])

        if not mysql_ssh_pass or not backup_ssh_pass:
            self.BF.printLog("[%s]:获取密码失败"%(self.dconf['xtrabackup_task_id']), 
                              self.dconf['full_log'], 'green')
            return False

        self.BF.runShell("chown -R %s:%s %s"%(backup_ssh_user,backup_ssh_user,backup_path))
        scp_cmd = ("""/usr/bin/sshpass -p {mysql_ssh_pass} /usr/bin/scp -o StrictHostKeyChecking=no -P {mysql_ssh_port} {local_xtrabackup_sh} {mysql_ssh_user}@{mysql_host}:/tmp"""
                  .format(mysql_ssh_pass = mysql_ssh_pass,
                          mysql_ssh_port = mysql_ssh_port,
                          local_xtrabackup_sh = self.dconf['local_xtrabackup_sh'],
                          mysql_ssh_user = mysql_ssh_user,
                          mysql_host = mysql_host))
        self.BF.printLog("[%s]%s"%(self.dconf['xtrabackup_task_id'], scp_cmd),
                          self.dconf['full_log'], 'green')
        subprocess.call(scp_cmd, stdout=subprocess.PIPE, shell=True)

        exec_cmd = ("""mkdir -p {tmpdir}; echo '{mysql_ssh_pass}' | sudo -S su -c '/bin/bash /tmp/{basename_local_xtrabackup_sh} {mysql_host} {mysql_port} {mysql_user} {mysql_pass} {backup_host} {backup_ssh_port} {backup_ssh_user} {backup_ssh_pass} {backup_path} {tmpdir}' """
                    .format(mysql_ssh_pass = mysql_ssh_pass,
                            backup_ssh_pass = backup_ssh_pass,
                            basename_local_xtrabackup_sh = os.path.basename(
                            self.dconf['local_xtrabackup_sh']),
                            mysql_host = self.dconf['f_info']['source_host'],
                            mysql_port = self.dconf['f_info']['source_port'],
                            mysql_user = self.dconf['dump_user'],
                            mysql_pass = self.dconf['dump_pass'],
                            backup_host = self.dconf['local_ip'],
                            backup_ssh_port = backup_ssh_port,
                            backup_ssh_user = backup_ssh_user,
                            backup_path = backup_path,
                            tmpdir = tmpdir)
        )
        remote_cmd = ("""/usr/bin/sshpass -p {mysql_ssh_pass} /usr/bin/ssh -t -o StrictHostKeyChecking=no -p {mysql_ssh_port} {mysql_ssh_user}@{mysql_host} "{cmd}" """
                     .format(mysql_ssh_pass = mysql_ssh_pass,
                             mysql_ssh_port = mysql_ssh_port,
                             mysql_ssh_user = mysql_ssh_user,
                             mysql_host = mysql_host,
                             cmd = exec_cmd))
        self.BF.printLog("[%s]%s"%(self.dconf['xtrabackup_task_id'], remote_cmd), 
                          self.dconf['full_log'], 'green')
        subprocess.call(remote_cmd, stdout=subprocess.PIPE, shell=True)
        return True
        

class BaseFunction(object):

    def __init__(self, t_conf_common=None, t_conf_person=None, conn_dbadb=None):

        self.t_conf_common = t_conf_common
        self.t_conf_person = t_conf_person
        self.conn_dbadb = conn_dbadb
        
    def connMySQL(self, sql=None, d=None, is_dict=1):
        """
        d = {'host': host, 'port': port, 'user': user, 'passwd': passwd}
        """
        # MySQLdb Warning升级为Error
        from warnings import filterwarnings
        filterwarnings('error', category = MySQLdb.Warning)
        try:
            if is_dict == 1:
                conn = MySQLdb.connect(host=d['host'], port=d['port'],
                        user=d['user'], passwd=d['passwd'],
                        db='information_schema', charset='utf8', 
                        cursorclass=MySQLdb.cursors.DictCursor)
            else:
                conn = MySQLdb.connect(host=d['host'], port=d['port'],
                        user=d['user'], passwd=d['passwd'],
                        db='information_schema', charset='utf8')
            cur = conn.cursor()
            cur.execute(sql)
            values = cur.fetchall()
            conn.commit()
            cur.close()
            conn.close()
            return values
        except Exception,e:
            raise Exception("sql is running error:%s..."%e)

    def addLog(self, content=None, normal_log=None):
        log = "%s/python.log"%(os.path.dirname(normal_log))
        try:
            logging.basicConfig(level = logging.DEBUG,
                    format = '[%(asctime)s %(filename)s]:%(message)s',
                    datefmt = '%Y-%m-%d %H:%M:%S',
                    filename = log,
                    filemode = 'a'
            )
            logging.info(content)
            content = str(content)
        except Exception,e:
            print e
            pass
    
    def printLog(self, content=None, normal_log=None, color='normal'):
        #self.addLog(content=content, normal_log=normal_log) # Tips:所有日志都入normal_log
        if normal_log:
            try:
                logging.basicConfig(level = logging.DEBUG,
                        format = '[%(asctime)s %(filename)s]:%(message)s',
                        datefmt = '%Y-%m-%d %H:%M:%S',
                        filename = normal_log,
                        filemode = 'a'
                )
                logging.info(content)
                content = str(content)
            except Exception,e:
                pass
        codeCodes = {
                'black':'0;30',
                'green':'0;32',
                'cyan':'0;36',
                'red':'0;31',
                'purple':'0;35',
                'normal':'0'
        }
        print("\033["+codeCodes[color]+"m"+'[%s] %s'%(time.strftime('%F %T',time.localtime()),content)+"\033[0m")


    def getSockFile(self, f, d):
        f = '%s/%s'%(d, f.replace('.py', '.sock.lock'))
        return f
   
    def runApplication(self, f, times):
        """
        run method : runApplication("app.pid", 10)
        """
        appInstance = ApplicationInstance(f)
        time.sleep(times) 
        appInstance.exitApplication()
    
    
    def getKV(self, k=None, ip=None, port=None):
        w_generl = "Fstate='online' and Fkey='%s'"%(k)
        s1 = "select Fvalue from %s where %s;"%(self.t_conf_common, w_generl)
        if not port:
            port = 65536
        s2 = "select Fvalue from %s where %s and Fserver_host='%s' and Fserver_port='%s';"%(self.t_conf_person, w_generl, ip, port)
        v1 = self.connMySQL(s1, self.conn_dbadb)
        v2 = self.connMySQL(s2, self.conn_dbadb)
        v = v2 if v2 else v1
        v = v[0]['Fvalue'] if v else False
        return v


    def getKVDict(self, ip=None, port=None, real=0):
        d = {}
        sql = ("""select Fkey as k,
                    Fvalue as v
                from %s
                where Fstate='online'"""
                %(self.t_conf_common))
        if real == 1:
            sql = "%s and Freal_state='Y'"%(sql)
        kvs = self.connMySQL(sql, self.conn_dbadb)
        for kv in kvs:
            k = kv['k']
            d[k] = self.getKV(k=k, ip=ip, port=port)
        return d

    def runShell(self, c):
        status, text = commands.getstatusoutput(c)
        return status, text

    def mkdirPath(self, path):
        try:
            os.makedirs(path)
        except OSError,e:
            pass


 
class ApplicationInstance(object):
 
    def __init__(self, pid_file):
        self.pid_file = pid_file
        self.check()
        self.startApplication()
 
    def check(self):
        if not os.path.isfile(self.pid_file):
            return
        pid = 0
        try:
            file = open(self.pid_file, 'rt')
            data = file.read()
            file.close()
            pid = int(data)
        except:
            pass
 
        if 0 == pid:
            return
 
        try:
            os.kill(pid, 0)
        except:
            return
 
        print "The application is already running !"
        exit(0) 
 
    def startApplication(self):
        file = open(self.pid_file, 'wt')
        file.write(str(os.getpid()))
        file.close()
 
    def exitApplication(self):
        try:
            os.remove(self.pid_file)
        except:
            pass
 


