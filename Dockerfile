# Dashboard financeiro — NC Decorações
# Imagem enxuta pra rodar scripts/dashboard.py (Flask) ao lado de outros
# containers no mesmo servidor (ex: n8n).

FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY scripts/ ./scripts/
COPY db/ ./db/

# DASHBOARD_ENV=production liga autenticação básica (ver scripts/dashboard.py)
# e desliga o modo debug do Flask. DASHBOARD_USER/DASHBOARD_SENHA não têm
# default aqui de propósito — precisam vir do ambiente em tempo de execução
# (docker run -e ... ou docker-compose), nunca hardcoded na imagem.
ENV DASHBOARD_ENV=production
ENV PORT=5000

EXPOSE 5000

# Nota: db/financeiro.db vai dentro da imagem pra simplificar o primeiro
# deploy, mas como é o dado real que muda com o tempo, o recomendado em
# produção é montar um volume sobre /app/db (ou pelo menos /app/db/financeiro.db)
# pra persistir entre rebuilds/restarts do container, ex:
#   docker run -v $(pwd)/db:/app/db ...

CMD ["python", "scripts/dashboard.py"]
