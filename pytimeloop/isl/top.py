from ctypes import *

isl = cdll.LoadLibrary("libbarvinok.so")
libc = cdll.LoadLibrary("libc.so.6")

class Error(Exception):
    pass

class Context:
    defaultInstance = None

    def __init__(self):
        ptr = isl.isl_ctx_alloc()
        self.ptr = ptr

    def __del__(self):
        isl.isl_ctx_free(self)

    def from_param(self):
        return c_void_p(self.ptr)

    @staticmethod
    def getDefaultInstance():
        if Context.defaultInstance == None:
            Context.defaultInstance = Context()
        return Context.defaultInstance

isl.isl_ctx_alloc.restype = c_void_p
isl.isl_ctx_free.argtypes = [Context]
