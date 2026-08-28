"""Usuários individuais do dashboard — complementa a senha mestra
(DASHBOARD_USER/DASHBOARD_SENHA no .env, que continua funcionando sempre)
com contas por pessoa. Sem distinção de permissão por ora: todo usuário
ativo tem acesso completo, igual à senha mestra.

Mesmo estilo de compromissos.py/cadastros.py: funções soltas recebendo
sqlite3.Connection, sem ORM. Nenhuma função aqui commita — quem chama
decide quando.
"""
import sqlite3

from werkzeug.security import check_password_hash, generate_password_hash


def listar_usuarios(conn: sqlite3.Connection) -> list[tuple]:
    return conn.execute(
        "SELECT id, nome, usuario, ativo FROM usuarios ORDER BY nome"
    ).fetchall()


def criar_usuario(conn: sqlite3.Connection, nome: str, usuario: str, senha: str) -> int:
    cursor = conn.execute(
        "INSERT INTO usuarios (nome, usuario, senha_hash) VALUES (?, ?, ?)",
        (nome.strip(), usuario.strip(), generate_password_hash(senha)),
    )
    return cursor.lastrowid


def editar_usuario(
    conn: sqlite3.Connection, id_: int, nome: str, ativo: bool, nova_senha: str | None,
) -> None:
    """Atualiza nome e status; troca a senha só se `nova_senha` vier
    preenchida (deixar em branco no formulário mantém a senha atual)."""
    if nova_senha:
        conn.execute(
            "UPDATE usuarios SET nome = ?, ativo = ?, senha_hash = ? WHERE id = ?",
            (nome.strip(), 1 if ativo else 0, generate_password_hash(nova_senha), id_),
        )
    else:
        conn.execute(
            "UPDATE usuarios SET nome = ?, ativo = ? WHERE id = ?",
            (nome.strip(), 1 if ativo else 0, id_),
        )


def autenticar(conn: sqlite3.Connection, usuario: str | None, senha: str | None) -> bool:
    if not usuario or not senha:
        return False
    linha = conn.execute(
        "SELECT senha_hash FROM usuarios WHERE usuario = ? AND ativo = 1", (usuario,)
    ).fetchone()
    if linha is None:
        return False
    return check_password_hash(linha[0], senha)
