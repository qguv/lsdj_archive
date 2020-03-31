from flask import request, redirect, url_for, flash, session, escape, g
from urllib.parse import urlparse, parse_qs
from uuid import uuid4

from functools import wraps

class AuthError(Exception):
    pass

class Auth:
    def __init__(self, redis, bcrypt):
        self.redis = redis
        self.bcrypt = bcrypt

    def is_authenticated(self):
        try:
            uid = session['u']

            # TODO TTL
            #epoch = datetime.utcnow().timestamp()
            #if epoch >= int(self.redis.hget(f'user:{uid}', 'token_exp')): return False

            return session['t'] != self.redis.hget(f'user:{uid}', 'token')

        except KeyError:
            return False

    def deauth(self):
        try:
            del session['t']
        except KeyError:
            pass

        try:
            uid = session['u']
            self.redis.hdel(f'user:{uid}', 'token')
        except KeyError:
            pass

        flash("Logged out.")

    def signup_form(self, *, success_redirect: str):
        def decorator(wrapped):
            @wraps(wrapped)
            def wrapping(*args, **kwargs):

                if request.method == 'POST':

                    try:
                        # TODO atomic
                        handle = request.form['h']
                        try:
                            self.redis.hget('handles', handle)
                        except KeyError:
                            pass
                        else:
                            raise AuthError("That handle is taken! Please choose a different one.")

                        # TODO TTL
                        token = uuid4()

                        uid = self.redis.incr('user:last')

                        phash = self.bcrypt.generate_password_hash(request.form['p'])

                        self.redis.hset('handles', handle, uid)
                        self.redis.hset(f'user:{uid}', 'handle', handle, 'token', token, 'phash', phash)

                        # store session info
                        session['h'] = handle
                        session['u'] = uid
                        session['t'] = token

                        flash(f"Welcome aboard, {escape(session['h'])}.")

                        try:
                            return_to = parse_qs(urlparse(request.referrer).query)['r'][0]
                            return redirect(f"/{return_to}" if return_to else url_for(success_redirect))
                        except KeyError:
                            return redirect(url_for(success_redirect))

                    # TODO query string will be lost
                    except AuthError as e:
                        flash(str(e))

                if request.args.get('r'):
                    flash("You need an account for that")

                # TODO auto-fill username if known
                return wrapped(*args, **kwargs)
            return wrapping
        return decorator

    def login_form(self, *, success_redirect: str):
        def decorator(wrapped):
            @wraps(wrapped)
            def wrapping(*args, **kwargs):

                if request.method == 'POST':

                    # get password hash
                    # TODO atomic
                    uid = self.redis.hget('handles', request.form['h'])
                    phash = self.redis.hget(f'user:{uid}', 'phash')

                    if self.bcrypt.check_password_hash(phash, request.form['p']):

                        # generate new token
                        # TODO TTL
                        token = str(uuid4())
                        self.redis.hset(f'user:{uid}', 'token', token)

                        # store session info
                        session['h'] = request.form['h']
                        session['u'] = uid
                        session['t'] = token

                        flash(f"Welcome, {escape(session['h'])}.")

                        try:
                            return_to = parse_qs(urlparse(request.referrer).query)['r'][0]
                            return redirect(f"/{return_to}" if return_to else url_for(success_redirect))
                        except KeyError:
                            return redirect(url_for(success_redirect))
                    else:
                        # TODO query string will be lost
                        flash('Invalid credentials!')

                if request.args.get('r'):
                    # TODO different messages for session timeout and unauthenticated
                    flash("You need to log in first.")

                # TODO auto-fill username if known
                return wrapped(*args, **kwargs)
            return wrapping
        return decorator

    def required(self, wrapped):
        @wraps(wrapped)
        def wrapping(*args, **kwargs):
            if is_authenticated():
                return wrapped(*args, **kwargs)
            else:
                return redirect(url_for('login', r=request.full_path.lstrip('/').rstrip('?')))
        return wrapping
