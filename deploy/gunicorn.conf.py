bind = "127.0.0.1:8000"          # exponha apenas pelo proxy reverso com HTTPS
workers = 2                       # SQLite serializa escritas; mais workers não aumentam a vazão de escrita
threads = 4
timeout = 60
forwarded_allow_ips = "127.0.0.1"
accesslog = "-"
