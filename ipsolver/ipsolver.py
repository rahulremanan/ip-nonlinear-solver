"""Trust-region interior points methods"""

from __future__ import division, print_function, absolute_import
import scipy.sparse as spc
import numpy as np
from .equality_constrained_sqp import equality_constrained_sqp
from numpy.linalg import norm
from scipy.sparse.linalg import LinearOperator

__all__ = ['BarrierSubproblem',
           'ipsolver']


class BarrierSubproblem:
    """
    Barrier optimization problem:

        minimize fun(x) - barrier_parameter*sum(log(s))
        subject to: constr_eq(x)     = 0
                      A_eq x + b     = 0
                       constr(x) + s = 0
                         A x + b + s = 0
                          x - ub + s = 0  (for ub != inf)
                          lb - x + s = 0  (for lb != -inf)
    """

    def __init__(self, x0, fun, grad, hess, constr, jac,
                 constr_eq, jac_eq, lb, ub, A, b, A_eq, b_eq,
                 barrier_parameter, tolerance, max_substep_iter):

        # Store parameters
        self.x0 = x0
        self.fun = fun
        self.grad = grad
        self.hess = hess
        self.constr = constr
        self.jac = jac
        self.constr_eq = constr_eq
        self.jac_eq = jac_eq
        self.A = A
        self.b = b
        self.A_eq = A_eq
        self.b_eq = b_eq
        self.barrier_parameter = barrier_parameter
        self.tolerance = tolerance
        self.max_substep_iter = max_substep_iter

        # Compute number of finite lower and upper bounds
        self.n_lb = np.sum(np.invert(np.isinf(lb)))
        self.n_ub = np.sum(np.invert(np.isinf(ub)))
        # Compute number of variables
        self.n_vars, = np.shape(x0)
        # Nonlinear constraints
        # TODO: Avoid this unecessary call to the constraints.
        self.n_eq, = np.shape(constr_eq(x0))
        self.n_ineq, = np.shape(constr(x0))
        # Linear constraints
        self.n_lin_eq, = np.shape(b_eq)
        self.n_lin_ineq, = np.shape(b)
        # Number of slack variables
        self.nslack = (self.n_lb + self.n_ub +
                       self.n_ineq + self.n_lin_ineq)

    def get_slack(self, z):
        return z[self.n_vars:self.n_vars+self.n_slack]

    def get_variables(self, z):
        return z[:self.n_vars]

    def s0(self):
        return np.ones(self.n_slack)

    def z0(self):
        return np.hstack((self.x0, self.s0()))

    def barrier_fun(self, z):
        """Returns barrier function at given point.

        For z = [x, s], returns barrier function:
            barrier_fun(z) = fun(x) - barrier_parameter*sum(log(s))
        """
        x = self.get_variables(z)
        s = self.get_slack(z)
        return self.fun(x) - self.barrier_parameter*np.sum(np.log(s))

    def constr_slack(self, z):
        """Returns barrier problem constraints at given points.

        For z = [x, s], returns the constraints:

            constr_slack(z) =[[   constr_eq(x) ]]
                             [[ A_eq x + b     ]]
                             [[  constr(x) + s ]]
                             [[    A x + b + s ]]
                             [[     x - ub + s ]]  (for ub != inf)
                             [[     lb - x + s ]]  (for lb != -inf)
        """
        x = self.get_variables(z)
        s = self.get_slack(z)
        return np.hstack((self.constr_eq(x),
                          self.A_eq.dot(x) + self.b_eq,
                          self.constr(x) + s,
                          self.A.dot(x) + self.b,
                          x - self.ub + s,
                          self.lb - x + s))

    def scaling(self, z):
        """Returns scaling vector.

        Given by:
            scaling = [ones(n_vars), s]
        """
        s = self.get_slack(z)
        diag_elements = np.hstack((np.ones(self.n_vars), s))

        # Diagonal Matrix
        def matvec(vec):
            return diag_elements*vec
        return LinearOperator((self.n_vars+self.n_slack,
                               self.n_vars+self.n_slack),
                              matvec)

    def barrier_grad(self, z):
        """Returns scaled gradient (for the barrier problem).

        The result of scaling the gradient
        of the barrier problem by the previously
        defined scaling factor:
            barrier_grad = [[             grad(x)             ]]
                           [[ -barrier_parameter*ones(n_ineq) ]]
        """
        x = self.get_variables(z)
        return np.hstack((self.grad(x),
                          -self.barrier_parameter*np.ones(self.n_slack)))

    def jac(self, z):
        """Returns scaled Jacobian.

        The result of scaling the Jacobian
        by the previously defined scaling factor:
            barrier_grad = [[  jac_eq(x)     0  ]]
                           [[  A_eq(x)       0  ]]
                           [[[ jac(x) ]         ]]
                           [[[   A    ]         ]]  
                           [[[   I    ]      S  ]]
                           [[[  -I    ]         ]]                
        """
        x = self.get_variables(z)
        s = self.get_slack(z)
        S = spc.diags((s,), (0,))
        I = spc.eye(self.nvars)

        aux = spc.bmat([[self.jac(x)],
                        [self.A]
                        [I],
                        [-I]])
        return spc.bmat([[self.jac_eq, None],
                         [self.A_eq, None],
                         [aux, S]], "csc")

    def lagr_hess_x(self, z, v):
        """Returns Lagrangian Hessian (in relation to variables ``x``)"""
        x = self.get_variables(z)
        # Get lagrange multipliers relatated to nonlinear equality constraints
        v_eq = v[:self.n_eq]
        # Get lagrange multipliers relatated to nonlinear ineq. constraints
        v_ineq = v[self.n_eq+self.n_lin_eq:self.n_eq+self.n_lin_eq+self.n_ineq]
        hess = self.hess
        return hess(x, v_eq, v_ineq)

    def lagr_hess_s(self, z, v):
        """Returns Lagrangian Hessian (in relation to slack variables ``s``)"""
        s = self.get_slack(z)
        # Using the primal formulation:
        #     lagr_hess_s = diag(1/s**2).
        # Reference [1]_ p. 882, formula (3.1)
        primal = self.barrier_parameter/(s*s)
        # Using the primal-dual formulation
        #     lagr_hess_s = diag(v/s)
        # Reference [1]_ p. 883, formula (3.11)
        primal_dual = v[-self.n_slack:]/s
        # Uses the primal-dual formulation for
        # positives values of v_ineq, and primal
        # formulation for the remaining ones.
        return np.where(v[-self.n_slack:] > 0, primal_dual, primal)

    def lagr_hess(self, z, v):
        """Returns scaled Lagrangian Hessian"""
        s = self.get_slack(z)
        # Compute Hessian in relation to x and s
        Hx = self.lagr_hess_x(z, v)
        Hs = self.lagr_hess_s(z, v)*s*s

        # The scaled Lagragian Hessian is:
        #     [[ Hx    0    ]]
        #     [[ 0   S Hs S ]]
        def matvec(vec):
            vec_x = self.get_variables(vec)
            vec_s = self.get_slack(vec)
            return np.hstack((Hx.dot(vec_x), Hs*vec_s))
        return LinearOperator((self.n_vars+self.n_slack,
                               self.n_vars+self.n_slack),
                              matvec)

    def stop_criteria(self, info):
        """Stop criteria to the barrier problem.

        The criteria here proposed is similar to formula (2.3)
        from [1]_, p.879.
        """
        if (info["opt"] < self.tolerance and info["constr_violation"] < self.tolerance) \
           or info["niter"] > self.max_iter:
            return True
        else:
            return False


def default_stop_criteria(info):
    if (info["opt"] < 1e-8 and info["constr_violation"] < 1e-8) \
       or info["niter"] > 1000:
        return True
    else:
        return False


def ipsolver(fun, grad, hess, constr, jac,
             constr_eq, jac_eq, lb, ub,
             A, b, A_eq, b_eq, x0, v0=None,
             stop_criteria=default_stop_criteria,
             initial_barrier_parameter=0.1,
             initial_tolerance=0.1,
             initial_penalty=1.0,
             initial_trust_radius=1.0,
             max_substep_iter=1000):
    """Trust-region interior points method.

    Solve problem:

        minimize fun(x)
        subject to: constr(x) <= 0
                  constr_eq(x) = 0
                          A x <= b
                        A_eq x = b
                         lb <= x <= ub

    References
    ----------
    .. [1] Byrd, Richard H., Mary E. Hribar, and Jorge Nocedal.
           "An interior point algorithm for large-scale nonlinear
           programming." SIAM Journal on Optimization 9.4 (1999): 877-900.
    .. [2] Byrd, Richard H., Guanghui Liu, and Jorge Nocedal.
           "On the local behavior of an interior point method for
           nonlinear programming." Numerical analysis 1997 (1997): 37-56.
    """
    # BOUNDARY_PARAMETER controls the decrease on the slack
    # variables. Represents ``tau`` from [1]_ p.885, formula (3.18).
    BOUNDARY_PARAMETER = 0.995
    # BARRIER_DECAY_RATIO controls the decay of the barrier parameter
    # and of the subproblem toloerance. Represents ``theta`` from [1]_ p.879.
    BARRIER_DECAY_RATIO = 0.2
    # TRUST_ENLARGEMENT controls the enlargement on trust radius
    # after each iteration
    TRUST_ENLARGEMENT = 5

    # Initial Values
    barrier_parameter = initial_barrier_parameter
    tolerance = initial_tolerance
    trust_radius = initial_trust_radius
    v = v0
    iteration = 0
    # Define barrier subproblem
    subprob = BarrierSubproblem(
            x0, fun, grad, hess, constr, jac, constr_eq, jac_eq,
            lb, ub, A, b, A_eq, b_eq, barrier_parameter, tolerance,
            max_substep_iter)
    # Define initial parameter for the first iteration.
    z = subprob.z0()
    # Define trust region bounds
    trust_lb = np.hstack((np.full(subprob.n_vars, -np.inf),
                          np.full(subprob.n_slack, -BOUNDARY_PARAMETER)))
    trust_ub = np.full(subprob.n_vars+subprob.n_slack, np.inf)
    while True:
        # Update Barrier Problem
        subprob.update(fun, grad, hess, constr, jac, constr_eq, jac_eq,
                       barrier_parameter, tolerance)
        # Solve SQP subproblem
        z, info = equality_constrained_sqp(
            subprob.barrier_fun,
            subprob.barrier_grad,
            subprob.lagr_hess,
            subprob.constr,
            subprob.jac,
            z, v,
            trust_radius,
            trust_lb,
            trust_ub,
            subprob.stop_criteria,
            initial_penalty,
            subprob.scaling)

        # Update parameters
        iteration += info["niter"]
        trust_radius = max(initial_trust_radius,
                           TRUST_ENLARGEMENT*info["trust_radius"])
        v0 = info["v"]
        # TODO: Use more advanced strategies from [2]_
        # to update this parameters.
        barrier_parameter = BARRIER_DECAY_RATIO*barrier_parameter
        tolerance = BARRIER_DECAY_RATIO*tolerance
        # Update info
        info['niter'] = iteration

        if stop_criteria(info):
            # Get x
            x = subprob.get_variables(z)
            break

    return x, info