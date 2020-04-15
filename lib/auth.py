from flask import request, redirect, url_for, flash, session, escape, g
from urllib.parse import urlparse, parse_qs
from uuid import uuid4, UUID
from datetime import datetime
from models.user import User

from functools import wraps


class AuthError(Exception):
    pass


class Auth:
    def __init__(self, db, redis, bcrypt, token_ttl):
        self.db = db
        self.redis = redis
        self.bcrypt = bcrypt
        self.token_ttl = token_ttl

    def is_authenticated(self):
        try:
            uid = session['u']
            token = session['t']
        except KeyError:
            return False

        try:
            uid = UUID(uid)
        except ValueError:
            del session['u']
            return False

        try:
            if token != self.redis[f'token:{uid}']:
                return False
        except KeyError:
            return False

        g.user = User.query.get(uid)
        if g.user is None:
            del session['t']
            return False

        return True

    def deauth(self):
        if not self.is_authenticated():
            return

        uid = session['u']

        self.redis.delete(f'token:{uid}')
        del session['t']
        flash("Logged out.")

    def generate_token(self, uid):
        token = str(uuid4())
        self.redis.setex(f'token:{uid}', self.token_ttl, token)
        return token

    def signup_form(self, *, success_redirect: str):
        def decorator(wrapped):
            @wraps(wrapped)
            def wrapping(*args, **kwargs):

                if request.method == 'POST':
                    try:
                        # TODO atomic
                        try:
                            handle = request.form['h']
                            password = request.form['p']
                            rid = request.form['r']
                        except KeyError:
                            raise AuthError()

                        if not rid:
                            raise AuthError()

                        if len(handle) < 3:
                            raise AuthError(
                                "Handle must be at least 3 characters!"
                            )

                        if self.redis.hget('handles', handle) is not None:
                            raise AuthError(
                                "That handle is taken! Please choose a "
                                "different one."
                            )

                        if len(password) < 8:
                            return AuthError(
                                "Password must be at least 8 "
                                "characters!"
                            )

                        ruid = self.redis.get(f'referral:{rid}')
                        if ruid is None:
                            raise AuthError("Referral code is not valid")
                        self.redis.delete(f'referral:{rid}')

                        uid = self.redis.incr('user_last')
                        phash = self.bcrypt.generate_password_hash(password)
                        token = self.generate_token(uid)
                        now = int(datetime.utcnow().timestamp())

                        self.redis.hset('handles', handle, uid)
                        self.redis.hset(f'user:{uid}', 'handle', handle)
                        self.redis.hset(f'user:{uid}', 'phash', phash)
                        self.redis.hset(f'user:{uid}', 'joined_at', now)
                        self.redis.hset(f'user:{uid}', 'referred_by', ruid)
                        self.redis.setex(
                            f'referral_cooldown:{uid}',
                            60 * 60 * 24,
                            1,
                        )

                        # store session info
                        session['h'] = handle
                        session['u'] = uid
                        session['t'] = token

                        flash(f"Welcome aboard, {escape(handle)}!")
                        return redirect(url_for(success_redirect))

                    except AuthError as e:
                        s = str(e) or (
                            "Please enter a referral code, handle, and "
                            "password."
                        )
                        flash(s)
                        # TODO auto-fill referral from last time

                # TODO auto-fill handle if known
                return wrapped(*args, **kwargs)
            return wrapping
        return decorator

    def login_form(self, *, success_redirect: str):
        def decorator(wrapped):
            @wraps(wrapped)
            def wrapping(*args, **kwargs):

                if request.method == 'POST':
                    try:

                        # TODO atomic
                        try:
                            handle = request.form['h']
                            password = request.form['p']
                        except KeyError:
                            raise AuthError()

                        uid = self.redis.hget('handles', handle)
                        if uid is None:
                            raise AuthError()

                        phash = self.redis.hget(f'user:{uid}', 'phash')
                        if phash is None:
                            raise AuthError()

                        if not self.bcrypt.check_password_hash(phash, password):  # noqa: E501
                            raise AuthError()

                        token = self.generate_token(uid)

                        # store session info
                        session['h'] = handle
                        session['u'] = uid
                        session['t'] = token

                        flash(f"Welcome back, {escape(handle)}!")

                        try:
                            query = urlparse(request.referrer).query
                            return_to = parse_qs(query)['r'][0]
                            return redirect(
                                f"/{return_to}" if return_to
                                else url_for(success_redirect)
                            )
                        except KeyError:
                            return redirect(url_for(success_redirect))

                    # TODO query string will be lost
                    except AuthError as e:
                        s = str(e)
                        flash(s if s else "Login incorrect.")

                if request.args.get('r'):
                    # TODO different messages for timeout and unauthenticated
                    flash("You need to log in first.")

                # TODO auto-fill handle if known
                return wrapped(*args, **kwargs)
            return wrapping
        return decorator

    def required(self):
        def decorator(wrapped):
            @wraps(wrapped)
            def wrapping(*args, **kwargs):
                if self.is_authenticated():
                    return wrapped(*args, **kwargs)
                else:
                    url = request.full_path.lstrip('/').rstrip('?')
                    return redirect(url_for('login', r=url))
            return wrapping
        return decorator
