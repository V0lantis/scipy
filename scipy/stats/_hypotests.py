from collections import namedtuple
from dataclasses import make_dataclass
import numpy as np
import warnings
from . import distributions
from ._continuous_distns import chi2, norm
from scipy.special import gamma, kv, gammaln
from . import _wilcoxon_data
import scipy.stats

Epps_Singleton_2sampResult = namedtuple('Epps_Singleton_2sampResult',
                                        ('statistic', 'pvalue'))


def epps_singleton_2samp(x, y, t=(0.4, 0.8)):
    """
    Compute the Epps-Singleton (ES) test statistic.

    Test the null hypothesis that two samples have the same underlying
    probability distribution.

    Parameters
    ----------
    x, y : array-like
        The two samples of observations to be tested. Input must not have more
        than one dimension. Samples can have different lengths.
    t : array-like, optional
        The points (t1, ..., tn) where the empirical characteristic function is
        to be evaluated. It should be positive distinct numbers. The default
        value (0.4, 0.8) is proposed in [1]_. Input must not have more than
        one dimension.

    Returns
    -------
    statistic : float
        The test statistic.
    pvalue : float
        The associated p-value based on the asymptotic chi2-distribution.

    See Also
    --------
    ks_2samp, anderson_ksamp

    Notes
    -----
    Testing whether two samples are generated by the same underlying
    distribution is a classical question in statistics. A widely used test is
    the Kolmogorov-Smirnov (KS) test which relies on the empirical
    distribution function. Epps and Singleton introduce a test based on the
    empirical characteristic function in [1]_.

    One advantage of the ES test compared to the KS test is that is does
    not assume a continuous distribution. In [1]_, the authors conclude
    that the test also has a higher power than the KS test in many
    examples. They recommend the use of the ES test for discrete samples as
    well as continuous samples with at least 25 observations each, whereas
    `anderson_ksamp` is recommended for smaller sample sizes in the
    continuous case.

    The p-value is computed from the asymptotic distribution of the test
    statistic which follows a `chi2` distribution. If the sample size of both
    `x` and `y` is below 25, the small sample correction proposed in [1]_ is
    applied to the test statistic.

    The default values of `t` are determined in [1]_ by considering
    various distributions and finding good values that lead to a high power
    of the test in general. Table III in [1]_ gives the optimal values for
    the distributions tested in that study. The values of `t` are scaled by
    the semi-interquartile range in the implementation, see [1]_.

    References
    ----------
    .. [1] T. W. Epps and K. J. Singleton, "An omnibus test for the two-sample
       problem using the empirical characteristic function", Journal of
       Statistical Computation and Simulation 26, p. 177--203, 1986.

    .. [2] S. J. Goerg and J. Kaiser, "Nonparametric testing of distributions
       - the Epps-Singleton two-sample test using the empirical characteristic
       function", The Stata Journal 9(3), p. 454--465, 2009.

    """

    x, y, t = np.asarray(x), np.asarray(y), np.asarray(t)
    # check if x and y are valid inputs
    if x.ndim > 1:
        raise ValueError('x must be 1d, but x.ndim equals {}.'.format(x.ndim))
    if y.ndim > 1:
        raise ValueError('y must be 1d, but y.ndim equals {}.'.format(y.ndim))
    nx, ny = len(x), len(y)
    if (nx < 5) or (ny < 5):
        raise ValueError('x and y should have at least 5 elements, but len(x) '
                         '= {} and len(y) = {}.'.format(nx, ny))
    if not np.isfinite(x).all():
        raise ValueError('x must not contain nonfinite values.')
    if not np.isfinite(y).all():
        raise ValueError('y must not contain nonfinite values.')
    n = nx + ny

    # check if t is valid
    if t.ndim > 1:
        raise ValueError('t must be 1d, but t.ndim equals {}.'.format(t.ndim))
    if np.less_equal(t, 0).any():
        raise ValueError('t must contain positive elements only.')

    # rescale t with semi-iqr as proposed in [1]; import iqr here to avoid
    # circular import
    from scipy.stats import iqr
    sigma = iqr(np.hstack((x, y))) / 2
    ts = np.reshape(t, (-1, 1)) / sigma

    # covariance estimation of ES test
    gx = np.vstack((np.cos(ts*x), np.sin(ts*x))).T  # shape = (nx, 2*len(t))
    gy = np.vstack((np.cos(ts*y), np.sin(ts*y))).T
    cov_x = np.cov(gx.T, bias=True)  # the test uses biased cov-estimate
    cov_y = np.cov(gy.T, bias=True)
    est_cov = (n/nx)*cov_x + (n/ny)*cov_y
    est_cov_inv = np.linalg.pinv(est_cov)
    r = np.linalg.matrix_rank(est_cov_inv)
    if r < 2*len(t):
        warnings.warn('Estimated covariance matrix does not have full rank. '
                      'This indicates a bad choice of the input t and the '
                      'test might not be consistent.')  # see p. 183 in [1]_

    # compute test statistic w distributed asympt. as chisquare with df=r
    g_diff = np.mean(gx, axis=0) - np.mean(gy, axis=0)
    w = n*np.dot(g_diff.T, np.dot(est_cov_inv, g_diff))

    # apply small-sample correction
    if (max(nx, ny) < 25):
        corr = 1.0/(1.0 + n**(-0.45) + 10.1*(nx**(-1.7) + ny**(-1.7)))
        w = corr * w

    p = chi2.sf(w, r)

    return Epps_Singleton_2sampResult(w, p)


class CramerVonMisesResult:
    def __init__(self, statistic, pvalue):
        self.statistic = statistic
        self.pvalue = pvalue

    def __repr__(self):
        return (f"{self.__class__.__name__}(statistic={self.statistic}, "
                f"pvalue={self.pvalue})")

def _psi1_mod(x):
    """
    psi1 is defined in equation 1.10 in Csorgo, S. and Faraway, J. (1996).
    This implements a modified version by excluding the term V(x) / 12
    (here: _cdf_cvm_inf(x) / 12) to avoid evaluating _cdf_cvm_inf(x)
    twice in _cdf_cvm.

    Implementation based on MAPLE code of Julian Faraway and R code of the
    function pCvM in the package goftest (v1.1.1), permission granted
    by Adrian Baddeley. Main difference in the implementation: the code
    here keeps adding terms of the series until the terms are small enough.
    """

    def _ed2(y):
        z = y**2 / 4
        b = kv(1/4, z) + kv(3/4, z)
        return np.exp(-z) * (y/2)**(3/2) * b / np.sqrt(np.pi)

    def _ed3(y):
        z = y**2 / 4
        c = np.exp(-z) / np.sqrt(np.pi)
        return c * (y/2)**(5/2) * (2*kv(1/4, z) + 3*kv(3/4, z) - kv(5/4, z))

    def _Ak(k, x):
        m = 2*k + 1
        sx = 2 * np.sqrt(x)
        y1 = x**(3/4)
        y2 = x**(5/4)

        e1 = m * gamma(k + 1/2) * _ed2((4 * k + 3)/sx) / (9 * y1)
        e2 = gamma(k + 1/2) * _ed3((4 * k + 1) / sx) / (72 * y2)
        e3 = 2 * (m + 2) * gamma(k + 3/2) * _ed3((4 * k + 5) / sx) / (12 * y2)
        e4 = 7 * m * gamma(k + 1/2) * _ed2((4 * k + 1) / sx) / (144 * y1)
        e5 = 7 * m * gamma(k + 1/2) * _ed2((4 * k + 5) / sx) / (144 * y1)

        return e1 + e2 + e3 + e4 + e5

    x = np.asarray(x)
    tot = np.zeros_like(x, dtype='float')
    cond = np.ones_like(x, dtype='bool')
    k = 0
    while np.any(cond):
        z = -_Ak(k, x[cond]) / (np.pi * gamma(k + 1))
        tot[cond] = tot[cond] + z
        cond[cond] = np.abs(z) >= 1e-7
        k += 1

    return tot


def _cdf_cvm_inf(x):
    """
    Calculate the cdf of the Cramér-von Mises statistic (infinite sample size).

    See equation 1.2 in Csorgo, S. and Faraway, J. (1996).

    Implementation based on MAPLE code of Julian Faraway and R code of the
    function pCvM in the package goftest (v1.1.1), permission granted
    by Adrian Baddeley. Main difference in the implementation: the code
    here keeps adding terms of the series until the terms are small enough.

    The function is not expected to be accurate for large values of x, say
    x > 4, when the cdf is very close to 1.
    """
    x = np.asarray(x)

    def term(x, k):
        # this expression can be found in [2], second line of (1.3)
        u = np.exp(gammaln(k + 0.5) - gammaln(k+1)) / (np.pi**1.5 * np.sqrt(x))
        y = 4*k + 1
        q = y**2 / (16*x)
        b = kv(0.25, q)
        return u * np.sqrt(y) * np.exp(-q) * b

    tot = np.zeros_like(x, dtype='float')
    cond = np.ones_like(x, dtype='bool')
    k = 0
    while np.any(cond):
        z = term(x[cond], k)
        tot[cond] = tot[cond] + z
        cond[cond] = np.abs(z) >= 1e-7
        k += 1

    return tot


def _cdf_cvm(x, n=None):
    """
    Calculate the cdf of the Cramér-von Mises statistic for a finite sample
    size n. If N is None, use the asymptotic cdf (n=inf)

    See equation 1.8 in Csorgo, S. and Faraway, J. (1996) for finite samples,
    1.2 for the asymptotic cdf.

    The function is not expected to be accurate for large values of x, say
    x > 2, when the cdf is very close to 1 and it might return values > 1
    in that case, e.g. _cdf_cvm(2.0, 12) = 1.0000027556716846.
    """
    x = np.asarray(x)
    if n is None:
        y = _cdf_cvm_inf(x)
    else:
        # support of the test statistic is [12/n, n/3], see 1.1 in [2]
        y = np.zeros_like(x, dtype='float')
        sup = (1./(12*n) < x) & (x < n/3.)
        # note: _psi1_mod does not include the term _cdf_cvm_inf(x) / 12
        # therefore, we need to add it here
        y[sup] = _cdf_cvm_inf(x[sup]) * (1 + 1./(12*n)) + _psi1_mod(x[sup]) / n
        y[x >= n/3] = 1

    if y.ndim == 0:
        return y[()]
    return y


def cramervonmises(rvs, cdf, args=()):
    """
    Perform the Cramér-von Mises test for goodness of fit.

    This performs a test of the goodness of fit of a cumulative distribution
    function (cdf) :math:`F` compared to the empirical distribution function
    :math:`F_n` of observed random variates :math:`X_1, ..., X_n` that are
    assumed to be independent and identically distributed ([1]_).
    The null hypothesis is that the :math:`X_i` have cumulative distribution
    :math:`F`.

    Parameters
    ----------
    rvs : array_like
        A 1-D array of observed values of the random variables :math:`X_i`.
    cdf : str or callable
        The cumulative distribution function :math:`F` to test the
        observations against. If a string, it should be the name of a
        distribution in `scipy.stats`. If a callable, that callable is used
        to calculate the cdf: ``cdf(x, *args) -> float``.
    args : tuple, optional
        Distribution parameters. These are assumed to be known; see Notes.

    Returns
    -------
    res : object with attributes
        statistic : float
            Cramér-von Mises statistic.
        pvalue :  float
            The p-value.

    See Also
    --------
    kstest

    Notes
    -----
    .. versionadded:: 1.6.0

    The p-value relies on the approximation given by equation 1.8 in [2]_.
    It is important to keep in mind that the p-value is only accurate if
    one tests a simple hypothesis, i.e. the parameters of the reference
    distribution are known. If the parameters are estimated from the data
    (composite hypothesis), the computed p-value is not reliable.

    References
    ----------
    .. [1] Cramér-von Mises criterion, Wikipedia,
           https://en.wikipedia.org/wiki/Cram%C3%A9r%E2%80%93von_Mises_criterion
    .. [2] Csorgo, S. and Faraway, J. (1996). The Exact and Asymptotic
           Distribution of Cramér-von Mises Statistics. Journal of the
           Royal Statistical Society, pp. 221-234.

    Examples
    --------

    Suppose we wish to test whether data generated by ``scipy.stats.norm.rvs``
    were, in fact, drawn from the standard normal distribution. We choose a
    significance level of alpha=0.05.

    >>> import numpy as np
    >>> from scipy import stats
    >>> np.random.seed(626)
    >>> x = stats.norm.rvs(size=500)
    >>> res = stats.cramervonmises(x, 'norm')
    >>> res.statistic, res.pvalue
    (0.06342154705518796, 0.792680516270629)

    The p-value 0.79 exceeds our chosen significance level, so we do not
    reject the null hypothesis that the observed sample is drawn from the
    standard normal distribution.

    Now suppose we wish to check whether the same samples shifted by 2.1 is
    consistent with being drawn from a normal distribution with a mean of 2.

    >>> y = x + 2.1
    >>> res = stats.cramervonmises(y, 'norm', args=(2,))
    >>> res.statistic, res.pvalue
    (0.4798693195559657, 0.044782228803623814)

    Here we have used the `args` keyword to specify the mean (``loc``)
    of the normal distribution to test the data against. This is equivalent
    to the following, in which we create a frozen normal distribution with
    mean 2.1, then pass its ``cdf`` method as an argument.

    >>> frozen_dist = stats.norm(loc=2)
    >>> res = stats.cramervonmises(y, frozen_dist.cdf)
    >>> res.statistic, res.pvalue
    (0.4798693195559657, 0.044782228803623814)

    In either case, we would reject the null hypothesis that the observed
    sample is drawn from a normal distribution with a mean of 2 (and default
    variance of 1) because the p-value 0.04 is less than our chosen
    significance level.

    """
    if isinstance(cdf, str):
        cdf = getattr(distributions, cdf).cdf

    vals = np.sort(np.asarray(rvs))

    if vals.size <= 1:
        raise ValueError('The sample must contain at least two observations.')
    if vals.ndim > 1:
        raise ValueError('The sample must be one-dimensional.')

    n = len(vals)
    cdfvals = cdf(vals, *args)

    u = (2*np.arange(1, n+1) - 1)/(2*n)
    w = 1/(12*n) + np.sum((u - cdfvals)**2)

    # avoid small negative values that can occur due to the approximation
    p = max(0, 1. - _cdf_cvm(w, n))

    return CramerVonMisesResult(statistic=w, pvalue=p)


def _get_wilcoxon_distr(n):
    """
    Distribution of counts of the Wilcoxon ranksum statistic r_plus (sum of
    ranks of positive differences).
    Returns an array with the counts/frequencies of all the possible ranks
    r = 0, ..., n*(n+1)/2
    """
    cnt = _wilcoxon_data.COUNTS.get(n)

    if cnt is None:
        raise ValueError("The exact distribution of the Wilcoxon test "
                         "statistic is not implemented for n={}".format(n))

    return np.array(cnt, dtype=int)


def _Aij(A, i, j):
    """Sum of upper-left and lower right blocks of contingency table"""
    # See [2] bottom of page 309
    return A[:i, :j].sum() + A[i+1:, j+1:].sum()


def _Dij(A, i, j):
    """Sum of lower-left and upper-right blocks of contingency table"""
    # See [2] bottom of page 309
    return A[i+1:, :j].sum() + A[:i, j+1:].sum()


def _P(A):
    """Twice the number of concordant pairs, excluding ties."""
    # See [2] bottom of page 309
    m, n = A.shape
    count = 0
    for i in range(m):
        for j in range(n):
            count += A[i, j]*_Aij(A, i, j)
    return count


def _Q(A):
    """Twice the number of discordant pairs, excluding ties."""
    # See [2] bottom of page 309
    m, n = A.shape
    count = 0
    for i in range(m):
        for j in range(n):
            count += A[i, j]*_Dij(A, i, j)
    return count


def _a_ij_Aij_Dij2(A):
    """A term that appears in the ASE of Kendall's tau and Somers' D"""
    # See [2] section 4: Modified ASEs to test the null hypothesis...
    m, n = A.shape
    count = 0
    for i in range(m):
        for j in range(n):
            count += A[i, j]*(_Aij(A, i, j) - _Dij(A, i, j))**2
    return count


def _tau_b(A):
    """Calculate Kendall's tau-b and p-value from contingency table"""
    # See [2] 2.2 and 4.2

    # contingency table must be truly 2D
    if A.shape[0] == 1 or A.shape[1] == 1:
        return np.nan, np.nan

    NA = A.sum()
    PA = _P(A)
    QA = _Q(A)
    Sri2 = (A.sum(axis=1)**2).sum()
    Scj2 = (A.sum(axis=0)**2).sum()
    denominator = (NA**2 - Sri2)*(NA**2 - Scj2)

    tau = (PA-QA)/(denominator)**0.5

    numerator = 4*(_a_ij_Aij_Dij2(A) - (PA - QA)**2 / NA)
    s02_tau_b = numerator/denominator
    if s02_tau_b == 0:  # Avoid divide by zero
        return tau, 0
    Z = tau/s02_tau_b**0.5
    p = 2*norm.sf(abs(Z))  # 2-sided p-value

    return tau, p


def _somers_d(A):
    """Calculate Somers' D and p-value from contingency table"""
    # See [3] page 1740

    # contingency table must be truly 2D
    if A.shape[0] <= 1 or A.shape[1] <= 1:
        return np.nan, np.nan

    NA = A.sum()
    NA2 = NA**2
    PA = _P(A)
    QA = _Q(A)
    Sri2 = (A.sum(axis=1)**2).sum()

    d = (PA - QA)/(NA2 - Sri2)

    S = _a_ij_Aij_Dij2(A) - (PA-QA)**2/NA
    if S == 0:  # Avoid divide by zero
        return d, 0
    Z = (PA - QA)/(4*(S))**0.5
    p = 2*norm.sf(abs(Z))  # 2-sided p-value

    return d, p


SomersDResult = make_dataclass("SomersDResult",
                               ("statistic", "pvalue", "table"))


def somersd(x, y=None):
    r"""
    Calculates Somers' D, an asymmetric measure of ordinal association

    Like Kendall's :math:`\tau`, Somers' :math:`D` is a measure of the
    correspondence between two rankings. Both statistics consider the
    difference between the number of concordant and discordant pairs in two
    rankings :math:`X` and :math:`Y`, and both are normalized such that values
    close  to 1 indicate strong agreement and values close to -1 indicate
    strong disagreement. They differ in how they are normalized. To show the
    relationship, Somers' :math:`D` can be defined in terms of Kendall's
    :math:`\tau_a`:

    .. math::
        D(Y|X) = \frac{\tau_a(X, Y)}{\tau_a(X, X)}

    Suppose the first ranking :math:`X` has :math:`r` distinct ranks and the
    second ranking :math:`Y` has :math:`s` distinct ranks. These two lists of
    :math:`n` rankings can also be viewed as an :math:`r \times s` contingency
    table in which element :math:`i, j` is the number of rank pairs with rank
    :math:`i` in ranking :math:`X` and rank :math:`j` in ranking :math:`Y`.
    Accordingly, `somersd` also allows the input data to be supplied as a
    single, 2D contingency table instead of as two separate, 1D rankings.

    Note that the definition of Somers' :math:`D` is asymmetric: in general,
    :math:`D(Y|X) \neq D(X|Y)`. ``somersd(x, y)`` calculates Somers'
    :math:`D(Y|X)`: the "row" variable :math:`X` is treated as an independent
    variable, and the "column" variable :math:`Y` is dependent. For Somers'
    :math:`D(X|Y)`, swap the input lists or transpose the input table.

    Parameters
    ----------
    x: array_like
        1D array of rankings, treated as the (row) independent variable.
        Alternatively, a 2D contingency table.
    y: array_like
        If `x` is a 1D array of rankings, `y` is a 1D array of rankings of the
        same length, treated as the (column) dependent variable.
        If `x` is 2D, `y` is ignored.

    Returns
    -------
    res : SomersDResult
        A `SomersDResult` object with the following fields:

            correlation : float
               The Somers' :math:`D` statistic.
            pvalue : float
               The two-sided p-value for a hypothesis test whose null
               hypothesis is an absence of association, :math:`D=0`.
               See notes for more information.
            table : 2D array
               The contingency table formed from rankings `x` and `y` (or the
               provided contingency table, if `x` is a 2D array)

    See Also
    --------
    kendalltau : Calculates Kendall's tau, another correlation measure.
    weightedtau : Computes a weighted version of Kendall's tau.
    spearmanr : Calculates a Spearman rank-order correlation coefficient.
    pearsonr : Calculates a Pearson correlation coefficient.

    Notes
    -----
    This function follows the contingency table approach of [2]_ and
    [3]_. *p*-values are computed based on an asymptotic approximation of
    the test statistic distribution under the null hypothesis :math:`D=0`.

    Theoretically, hypothesis tests based on Kendall's :math:`tau` and Somers'
    :math:`D` should be identical.
    However, the *p*-values returned by `kendalltau` are based
    on the null hypothesis of *independence* between :math:`X` and :math:`Y`
    (i.e. the population from which pairs in :math:`X` and :math:`Y` are
    sampled contains equal numbers of all possible pairs), which is more
    specific than the null hypothesis :math:`D=0` used here. If the null
    hypothesis of independence is desired, it is acceptable to use the
    *p*-value returned by `kendalltau` with the statistic returned by
    `somersd` and vice versa. For more information, see [2]_.

    Contingency tables are formatted according to the convention used by
    SAS and R: the first ranking supplied (``x``) is the "row" variable, and
    the second ranking supplied (``y``) is the "column" variable. This is
    opposite the convention of Somers' original paper [1]_.

    References
    ----------
    .. [1] Robert H. Somers, "A New Asymmetric Measure of Association for
           Ordinal Variables", *American Sociological Review*, Vol. 27, No. 6,
           pp. 799--811, 1962.

    .. [2] Morton B. Brown and Jacqueline K. Benedetti, "Sampling Behavior of
           Tests for Correlation in Two-Way Contingency Tables", *Journal of
           the American Statistical Association* Vol. 72, No. 358, pp.
           309--315, 1977.

    .. [3] SAS Institute, Inc., "The FREQ Procedure (Book Excerpt)",
           *SAS/STAT 9.2 User's Guide, Second Edition*, SAS Publishing, 2009.

    .. [4] Laerd Statistics, "Somers' d using SPSS Statistics", *SPSS
           Statistics Tutorials and Statistical Guides*,
           https://statistics.laerd.com/spss-tutorials/somers-d-using-spss-statistics.php,
           Accessed July 31, 2020.

    Examples
    --------
    We calculate Somers' D for the example given in [4]_, in which a hotel
    chain owner seeks to determine the association between hotel room
    cleanliness and customer satisfaction. The independent variable, hotel
    room cleanliness, is ranked on an ordinal scale: "below average (1)",
    "average (2)", or "above average (3)". The dependent variable, customer
    satisfaction, is ranked on a second scale: "very dissatisfied (1)",
    "moderately dissatisfied (2)", "neither dissatisfied nor satisfied (3)",
    "moderately satisfied (4)", or "very satisfied (5)". 189 customers
    respond to the survey, and the results are cast into a contingency table
    with the hotel room cleanliness as the "row" variable and customer
    satisfaction as the "column" variable.

    +-----+-----+-----+-----+-----+-----+
    |     | (1) | (2) | (3) | (4) | (5) |
    +=====+=====+=====+=====+=====+=====+
    | (1) | 27  | 25  | 14  | 7   | 0   |
    +-----+-----+-----+-----+-----+-----+
    | (2) | 7   | 14  | 18  | 35  | 12  |
    +-----+-----+-----+-----+-----+-----+
    | (3) | 1   | 3   | 2   | 7   | 17  |
    +-----+-----+-----+-----+-----+-----+

    For example, 27 customers assigned their room a cleanliness ranking of
    "below average (1)" and a corresponding satisfaction of "very
    dissatisfied (1)". We perform the analysis as follows.

    >>> from scipy.stats import somersd
    >>> table = [[27, 25, 14, 7, 0], [7, 14, 18, 35, 12], [1, 3, 2, 7, 17]]
    >>> res = somersd(table)
    >>> res.statistic
    0.6032766111513396
    >>> res.pvalue
    1.0007091191074533e-27

    The value of the Somers' D statistic is approximately 0.6, indicating
    a positive correlation between room cleanliness and customer satisfaction
    in the sample.
    The *p*-value is very small, indicating a very small probability of
    observing such an extreme value of the statistic under the null
    hypothesis that the statistic of the entire population (from which
    our sample of 189 customers is drawn) is zero. This supports the
    alternative hypothesis that the true value of Somers' D for the population
    is nonzero.
    """
    x, y = np.array(x), np.array(y)
    if x.ndim == 1:
        if x.size != y.size:
            raise ValueError("Rankings must be of equal length.")
        table = scipy.stats.contingency.crosstab(x, y)[1]
    elif x.ndim == 2:
        if np.any(x < 0):
            raise ValueError("All elements of the contingency table must be "
                             "non-negative.")
        if np.any(x != x.astype(int)):
            raise ValueError("All elements of the contingency table must be "
                             "integer.")
        if x.nonzero()[0].size < 2:
            raise ValueError("At least two elements of the contingency table "
                             "must be nonzero.")
        table = x
    else:
        raise ValueError("x must be either a 1D or 2D array")
    d, p = _somers_d(table)
    return SomersDResult(d, p, table)


def _compute_log_combinations(n):
    """Compute all log combination of C(n, k)"""
    gammaln_arr = gammaln(np.arange(n + 1) + 1)
    return gammaln(n + 1) - gammaln_arr - gammaln_arr[::-1]


BarnardExactResult = make_dataclass(
    "BarnardExactResult", [("statistic", float), ("pvalue", float)]
)


def barnard_exact(table, alternative="two-sided", pooled=True, n_iter=3):
    r"""Perform a Barnard exact test on a 2x2 contingency table.

    Parameters
    ----------
    table : array_like of ints
        A 2x2 contingency table.  Elements should be non-negative integers.
    alternative : {'two-sided', 'less', 'greater'}, optional
        Defines the alternative hypothesis.
        The following options are available (default is 'two-sided'):

        * 'two-sided'
        * 'less': one-sided
        * 'greater': one-sided

    pooled : bool, optional
        Whether to compute score statistic with pooled variance (Student's
        t-test) or unpooled variance (Welch's t-test). Default is ``True`` :
        Statistic test is computed using pooled variance.

    n_iter : int, optional
        Number of iterations of the grid search. Default is 3. Must be
        non-negative. In most cases, 3 iterations is perfectly enough to
        reach good precision. Above a certain number (around
        6 iterations), the result will not change anymore. Note that every
        iteration added comes with a performance cost.

    Returns
    -------
    ber : BarnardExactResult
        The object has attributes `statistic` and `pvalue`. `statistic` is
        the Wald's statistic with pooled or unpooled variance, depending on
        the user choice of `pooled` param. `pvalue` is the probability of
        obtaining a test statistic at least as extreme as the one that was
        actually observed, assuming that the null hypothesis is true.

    See Also
    --------
    chi2_contingency : Chi-square test of independence of variables in a
        contingency table.
    fisher_exact : Fisher exact test on a 2x2 contingency table.

    Notes
    -----
    Barnard's test is an exact test used in the analysis of contingency
    tables. It examines the association of two categorical variables and
    is a more powerful alternative than Fisher's exact test
    for 2x2 contingency tables.
    When using Barnard exact test, we can assert three different null
    hypothesis :

    - :math:`H_0 : p_1 \geq p_2` versus :math:`H_1 : p_1 < p_2`,
      with `alternative` = "less"

    - :math:`H_0 : p_1 \leq p_2` versus :math:`H_1 : p_1 > p_2`,
      with `alternative` = "greater"

    - :math:`H_0 : p_1 = p_2` versus :math:`H_1 : p_1 \neq p_2`,
      with `alternative` = "two-sided" (default one)

    In order to compute Barnard's exact test, we are using the Wald
    statistic [3]_ with pooled and unpooled variance.
    Under the assumption that both variances are equals, use pooled variance
    (``pooled = True``). Otherwise, use the unpooled variance (``pooled =
    False``). With pooled variance, the statistic is computed as follow:

    .. math::

        T(X) = \frac{
            \hat{p_1} - \hat{p_2}
        }{
            \sqrt{
                \hat{p}(1 - \hat{p})
                (\frac{1}{c_1} +
                \frac{1}{c_2})
            }
        }

    and with unpooled variance :

    .. math::

        T(X) = \frac{
            \hat{p_1} - \hat{p_2}
        }{
            \sqrt{
                \frac{\hat{p_1}(1 - \hat{p_1})}{c_1} +
                \frac{\hat{p_2}(1 - \hat{p_2})}{c_2}
            }
        }

    References
    ----------
    .. [1] G. A. BARNARD, SIGNIFICANCE TESTS FOR 2x2 TABLES, Biometrika,
           Volume 34, Issue 1-2, January 1947, Pages 123-138,
           :doi:`dpgkg3`

    .. [2] Mehta, Cyrus & Senchaudhuri, Pralay. (2003).
           "Conditional versus Unconditional Exact Tests for Comparing Two
           Binomials"

    .. [3] https://en.wikipedia.org/wiki/Wald_test

    Examples
    --------
    An example use of Barnard's test is presented in [2]_.

        Consider the following example of a vaccine efficacy study
        (Chan, 1998). In a randomized clinical trial of 30 subjects, 15 were
        inoculated with a recombinant DNA influenza vaccine and the 15 were
        inoculated with a placebo. Twelve of the 15 subjects in the placebo
        group (80%) eventually became infected with influenza whereas for the
        vaccine group, only 7 of the 15 subjects (47%) became infected. The
        data are tabulated as a 2 x 2 table::

                Vaccine  Placebo
            Yes     7        12
            No      8        3

    When working with statistical hypothesis testing, we usually use a
    threshold probability or a significance level upon which we decide
    to reject or not the null hypothesis :math:`H_0`. A commonly used
    significance level is 5%. In this example, we are using the
    `alternative` parameter with the "less" option, because the vaccine has
    either no effect (:math:`H_0` hypothesis) or a positive effect
    (:math:`H_1` hypothesis). Therefore, under the null hypothesis
    (:math:`H_0`), the probability :math:`p_2` of catching the virus
    **without** any vaccine, is either equal or lower than :math:`p_1`,
    the probability of catching the virus **with** the vaccine.
    To compute the p-value with Banard test, let's call `barnard_exact` as
    follow :

    >>> import scipy.stats as stats
    >>> res = stats.barnard_exact([[7, 12], [8, 3]], alternative="less")
    >>> res.statistic
    -1.894...
    >>> res.pvalue
    0.03407...

    Then, the probability of obtaining test results at least as extreme as the
    data observed above is around 3.4%. Since our p-value is under our
    significance level defined above, we reject :math:`H_0`. The vaccine has
    a positive effect and we have :math:`p_1 \leq p_2`; the probability of
    contracting the virus if we have been vaccinated is lower than if
    we are not.

    We can compare with Fisher's exact test which produces an exact
    p-value of 6.4% :

    >>> _, pvalue = stats.fisher_exact([[7, 12], [8, 3]], alternative="less")
    >>> pvalue
    0.0640...

    With the same threshold probability of 5%, `fisher_exact` is accepting
    :math:`H_0` while `barnard_exact` is rejecting it. As stated in [2]_,
    Barnard's test is uniformly more powerful than Fisher's exact test
    because Barnard's test do not condition on any margin. Fisher's test
    should only be used when both sets of marginals are fixed.

    """
    if n_iter <= 0:
        raise ValueError(
            "Number of iterations `num_it` must be strictly positive, "
            f"found {n_iter!r}"
        )

    table = np.asarray(table, dtype=np.int64)

    if not table.shape == (2, 2):
        raise ValueError("The input `table` must be of shape (2, 2).")

    if np.any(table < 0):
        raise ValueError("All values in `table` must be nonnegative.")

    if 0 in table.sum(axis=0):
        # If both values in column are zero, the p-value is 1 and
        # the score's statistic is NaN.
        return BarnardExactResult(np.nan, 1.0)

    total_col_1, total_col_2 = table.sum(axis=0)

    x1 = np.arange(total_col_1 + 1, dtype=np.int64).reshape(-1, 1)
    x2 = np.arange(total_col_2 + 1, dtype=np.int64).reshape(1, -1)

    # We need to calculate the wald statistics for each combination of x1 and
    # x2.
    p1, p2 = x1 / total_col_1, x2 / total_col_2

    if pooled:
        p = (x1 + x2) / (total_col_1 + total_col_2)
        variances = p * (1 - p) * (1 / total_col_1 + 1 / total_col_2)
    else:
        variances = p1 * (1 - p1) / total_col_1 + p2 * (1 - p2) / total_col_2

    # To avoid warning when dividing by 0
    with np.errstate(divide="ignore", invalid="ignore"):
        wald_statistic = np.divide((p1 - p2), np.sqrt(variances))

    wald_statistic[p1 == p2] = 0  # Removing NaN values

    wald_stat_obs = wald_statistic[table[0, 0], table[0, 1]]

    if alternative == "two-sided":
        index_arr = np.abs(wald_statistic) >= abs(wald_stat_obs)
    elif alternative == "less":
        index_arr = wald_statistic <= wald_stat_obs
    elif alternative == "greater":
        index_arr = wald_statistic >= wald_stat_obs
    else:
        msg = (
            "`alternative` should be one of {'two-sided', 'less', 'greater'},"
            f" found {alternative!r}"
        )
        raise ValueError(msg)

    p_value = _binomial_maximisation_of_p_value_with_nuisance_param(
        total_col_1, total_col_2, index_arr, n_iter
    )
    return BarnardExactResult(wald_stat_obs, p_value)


def _binomial_maximisation_of_p_value_with_nuisance_param(
    total_c1, total_c2, idx, n_iter
):
    r"""
    Maximisation of the pvalue in respect of a nuisance parameter considering
    a 2x2 sample space.

    Barnard exact test iterate over a nuisance parameter
    :math:`\pi \in [0, 1]` to find the maximum p-value. To search this
    maxima, I am using a grid search algorithm, reducing :math:`\pi`
    bounds after each
    iterations `n_iter`. To compute the p-value, I am using numpy ndarrays
    which offer powerful broadcast operations. The different nuisance
    parameters are stored in an ndarray of length 100 and of shape (1, 1,
    100). Also, to compute the different combination used in the
    p-values' computation formula, uses `gammaln` which is
    more tolerant for large value than `scipy.special.comb`. This gives
    a log combination. For the little precision lost, we gain a lot of
    performance.
    """
    n = total_c1 + total_c2
    x1 = np.arange(total_c1 + 1, dtype=np.int64).reshape(-1, 1, 1)
    x2 = np.arange(total_c2 + 1, dtype=np.int64).reshape(1, -1, 1)

    x1_log_comb = _compute_log_combinations(total_c1)
    x2_log_comb = _compute_log_combinations(total_c2)
    x1_sum_x2_log_comb = x1_log_comb[x1] + x2_log_comb[x2]

    nuisance_num = 100
    inf_bound, sup_bound = 0, 1

    for _ in range(n_iter):
        nuisance_arr = np.linspace(
            start=inf_bound, stop=sup_bound, num=nuisance_num
        )
        # Reshape in dimension 3 array
        nuisance_arr = nuisance_arr.reshape(1, 1, -1)

        with np.errstate(divide="ignore", invalid="ignore"):
            log_nuisance_arr = np.log(
                nuisance_arr,
                out=np.zeros_like(nuisance_arr),
                where=nuisance_arr >= 0,
            )
            log_1_minus_nuisance_arr = np.log(
                1 - nuisance_arr,
                out=np.zeros_like(nuisance_arr),
                where=1 - nuisance_arr >= 0,
            )

            nuisance_power_x1_x2 = log_nuisance_arr * (x1 + x2)
            nuisance_power_x1_x2[(x1 + x2 == 0)[:, :, 0]] = 0

            nuisance_power_n_minus_x1_x2 = log_1_minus_nuisance_arr * (
                n - x1 - x2
            )
            nuisance_power_n_minus_x1_x2[(x1 + x2 == n)[:, :, 0]] = 0

            tmp_values_arr = np.exp(
                x1_sum_x2_log_comb
                + nuisance_power_x1_x2
                + nuisance_power_n_minus_x1_x2
            )

        tmp_values_arr /= tmp_values_arr.sum(axis=(0, 1)).reshape(1, 1, -1)
        # This operation compensate numerical errors because sums of
        # p_values_arr should always be equal to one.

        p_values_arr = tmp_values_arr[idx].sum(axis=0)  # Just sum where TX >= TX0
        max_pvalue_index = p_values_arr.argmax()

        inf_bound = (
            nuisance_arr[0, 0, max_pvalue_index - 1]
            if max_pvalue_index > 0
            else nuisance_arr[0, 0, 0]
        )
        sup_bound = (
            nuisance_arr[0, 0, max_pvalue_index + 1]
            if max_pvalue_index < nuisance_num - 1
            else nuisance_arr[0, 0, -1]
        )

    p_value = p_values_arr[max_pvalue_index]  # take the max value

    if p_value > 1:
        # Occurs because of numerical errors
        p_value = 1.0

    return p_value
