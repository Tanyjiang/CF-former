
import torch

M_EPS = 1e-16


def sinkhorn(a, b, C, reg=1e-1, method='sinkhorn', maxIter=1000, tau=1e3,
             stopThr=1e-9, verbose=False, log=True, warm_start=None, eval_freq=10, print_freq=200, **kwargs):

    if method.lower() == 'sinkhorn':
        return sinkhorn_knopp(a, b, C, reg, maxIter=maxIter,
                              stopThr=stopThr, verbose=verbose, log=log,
                              warm_start=warm_start, eval_freq=eval_freq, print_freq=print_freq,
                              **kwargs)
    elif method.lower() == 'sinkhorn_stabilized':
        return sinkhorn_stabilized(a, b, C, reg, maxIter=maxIter, tau=tau,
                                   stopThr=stopThr, verbose=verbose, log=log,
                                   warm_start=warm_start, eval_freq=eval_freq, print_freq=print_freq,
                                   **kwargs)
    elif method.lower() == 'sinkhorn_epsilon_scaling':
        return sinkhorn_epsilon_scaling(a, b, C, reg,
                                        maxIter=maxIter, maxInnerIter=100, tau=tau,
                                        scaling_base=0.75, scaling_coef=None, stopThr=stopThr,
                                        verbose=False, log=log, warm_start=warm_start, eval_freq=eval_freq,
                                        print_freq=print_freq, **kwargs)
    else:
        raise ValueError("Unknown method '%s'." % method)


def sinkhorn_knopp(a, b, C, reg=1e-1, maxIter=1000, stopThr=1e-9,
                   verbose=False, log=False, warm_start=None, eval_freq=10, print_freq=200, **kwargs):
    device = a.device
    na, nb = C.shape

    assert na >= 1 and nb >= 1, 'C needs to be 2d'
    assert na == a.shape[0] and nb == b.shape[0], "Shape of a or b does't match that of C"
    assert reg > 0, 'reg should be greater than 0'
    assert a.min() >= 0. and b.min() >= 0., 'Elements in a or b less than 0'

    if log:
        log = {'err': []}

    if warm_start is not None:
        u = warm_start['u']
        v = warm_start['v']
    else:
        u = torch.ones(na, dtype=a.dtype).to(device) / na
        v = torch.ones(nb, dtype=b.dtype).to(device) / nb

    K = torch.empty(C.shape, dtype=C.dtype).to(device)
    torch.div(C, -reg, out=K)
    torch.exp(K, out=K)

    b_hat = torch.empty(b.shape, dtype=C.dtype).to(device)

    it = 1
    err = 1

    # allocate memory beforehand
    KTu = torch.empty(v.shape, dtype=v.dtype).to(device)
    Kv = torch.empty(u.shape, dtype=u.dtype).to(device)

    while (err > stopThr and it <= maxIter):
        upre, vpre = u, v
        torch.matmul(u, K, out=KTu)
        v = torch.div(b, KTu + M_EPS)
        torch.matmul(K, v, out=Kv)
        u = torch.div(a, Kv + M_EPS)

        if torch.any(torch.isnan(u)) or torch.any(torch.isnan(v)) or \
                torch.any(torch.isinf(u)) or torch.any(torch.isinf(v)):
            print('Warning: numerical errors at iteration', it)
            u, v = upre, vpre
            break

        if log and it % eval_freq == 0:
            # we can speed up the process by checking for the error only all
            # the eval_freq iterations
            # below is equivalent to:
            # b_hat = torch.sum(u.reshape(-1, 1) * K * v.reshape(1, -1), 0)
            # but with more memory efficient
            b_hat = torch.matmul(u, K) * v
            err = (b - b_hat).pow(2).sum().item()
            # err = (b - b_hat).abs().sum().item()
            log['err'].append(err)

        if verbose and it % print_freq == 0:
            print('iteration {:5d}, constraint error {:5e}'.format(it, err))

        it += 1

    if log:
        log['u'] = u
        log['v'] = v
        log['alpha'] = reg * torch.log(u + M_EPS)
        log['beta'] = reg * torch.log(v + M_EPS)

    # transport plan
    P = u.reshape(-1, 1) * K * v.reshape(1, -1)
    if log:
        return P, log
    else:
        return P


def sinkhorn_stabilized(a, b, C, reg=1e-1, maxIter=1000, tau=1e3, stopThr=1e-9,
                        verbose=False, log=False, warm_start=None, eval_freq=10, print_freq=200, **kwargs):
    
    device = a.device
    na, nb = C.shape

    assert na >= 1 and nb >= 1, 'C needs to be 2d'
    assert na == a.shape[0] and nb == b.shape[0], "Shape of a or b does't match that of C"
    assert reg > 0, 'reg should be greater than 0'
    assert a.min() >= 0. and b.min() >= 0., 'Elements in a or b less than 0'

    if log:
        log = {'err': []}

    if warm_start is not None:
        alpha = warm_start['alpha']
        beta = warm_start['beta']
    else:
        alpha = torch.zeros(na, dtype=a.dtype).to(device)
        beta = torch.zeros(nb, dtype=b.dtype).to(device)

    u = torch.ones(na, dtype=a.dtype).to(device) / na
    v = torch.ones(nb, dtype=b.dtype).to(device) / nb

    def update_K(alpha, beta):
        torch.add(alpha.reshape(-1, 1), beta.reshape(1, -1), out=K)
        torch.add(K, -C, out=K)
        torch.div(K, reg, out=K)
        torch.exp(K, out=K)

    def update_P(alpha, beta, u, v, ab_updated=False):
        torch.add(alpha.reshape(-1, 1), beta.reshape(1, -1), out=P)
        torch.add(P, -C, out=P)
        torch.div(P, reg, out=P)
        if not ab_updated:
            torch.add(P, torch.log(u + M_EPS).reshape(-1, 1), out=P)
            torch.add(P, torch.log(v + M_EPS).reshape(1, -1), out=P)
        torch.exp(P, out=P)

    K = torch.empty(C.shape, dtype=C.dtype).to(device)
    update_K(alpha, beta)

    b_hat = torch.empty(b.shape, dtype=C.dtype).to(device)

    it = 1
    err = 1
    ab_updated = False

    # allocate memory beforehand
    KTu = torch.empty(v.shape, dtype=v.dtype).to(device)
    Kv = torch.empty(u.shape, dtype=u.dtype).to(device)
    P = torch.empty(C.shape, dtype=C.dtype).to(device)

    while (err > stopThr and it <= maxIter):
        upre, vpre = u, v
        torch.matmul(u, K, out=KTu)
        v = torch.div(b, KTu + M_EPS)
        torch.matmul(K, v, out=Kv)
        u = torch.div(a, Kv + M_EPS)

        ab_updated = False
        if u.abs().sum() > tau or v.abs().sum() > tau:
            alpha += reg * torch.log(u + M_EPS)
            beta += reg * torch.log(v + M_EPS)
            u.fill_(1. / na)
            v.fill_(1. / nb)
            update_K(alpha, beta)
            ab_updated = True

        if log and it % eval_freq == 0:
            update_P(alpha, beta, u, v, ab_updated)
            b_hat = torch.sum(P, 0)
            err = (b - b_hat).pow(2).sum().item()
            log['err'].append(err)

        if verbose and it % print_freq == 0:
            print('iteration {:5d}, constraint error {:5e}'.format(it, err))

        it += 1

    if log:
        log['u'] = u
        log['v'] = v
        log['alpha'] = alpha + reg * torch.log(u + M_EPS)
        log['beta'] = beta + reg * torch.log(v + M_EPS)

    # transport plan
    update_P(alpha, beta, u, v, False)

    if log:
        return P, log
    else:
        return P


def sinkhorn_epsilon_scaling(a, b, C, reg=1e-1, maxIter=100, maxInnerIter=100, tau=1e3, scaling_base=0.75,
                             scaling_coef=None, stopThr=1e-9, verbose=False, log=False, warm_start=None, eval_freq=10,
                             print_freq=200, **kwargs):
    na, nb = C.shape

    assert na >= 1 and nb >= 1, 'C needs to be 2d'
    assert na == a.shape[0] and nb == b.shape[0], "Shape of a or b does't match that of C"
    assert reg > 0, 'reg should be greater than 0'
    assert a.min() >= 0. and b.min() >= 0., 'Elements in a or b less than 0'

    def get_reg(it, reg, pre_reg):
        if it == 1:
            return scaling_coef
        else:
            if (pre_reg - reg) * scaling_base < M_EPS:
                return reg
            else:
                return (pre_reg - reg) * scaling_base + reg

    if scaling_coef is None:
        scaling_coef = C.max() + reg

    it = 1
    err = 1
    running_reg = scaling_coef

    if log:
        log = {'err': []}

    warm_start = None

    while (err > stopThr and it <= maxIter):
        running_reg = get_reg(it, reg, running_reg)
        P, _log = sinkhorn_stabilized(a, b, C, running_reg, maxIter=maxInnerIter, tau=tau,
                                      stopThr=stopThr, verbose=False, log=True,
                                      warm_start=warm_start, eval_freq=eval_freq, print_freq=print_freq,
                                      **kwargs)

        warm_start = {}
        warm_start['alpha'] = _log['alpha']
        warm_start['beta'] = _log['beta']

        primal_val = (C * P).sum() + reg * (P * torch.log(P)).sum() - reg * P.sum()
        dual_val = (_log['alpha'] * a).sum() + (_log['beta'] * b).sum() - reg * P.sum()
        err = primal_val - dual_val
        log['err'].append(err)

        if verbose and it % print_freq == 0:
            print('iteration {:5d}, constraint error {:5e}'.format(it, err))

        it += 1

    if log:
        log['alpha'] = _log['alpha']
        log['beta'] = _log['beta']
        return P, log
    else:
        return P
