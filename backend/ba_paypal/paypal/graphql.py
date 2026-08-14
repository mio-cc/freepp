"""GraphQL query and mutation definitions for PayPal checkout flow."""

# Member buyer identity queries (ported from paypal-agreement-protocol:
# identity-elevation / buyer-context sync).  These attach the signed-up member
# identity to an EC checkout before the final billing.authorize mutation.
BUYER_CONTEXT_QUERY = """
query BuyerContextQuery($token: String!) {
  checkoutSession(token: $token) {
    buyer {
      userId
      auth {
        accessToken
        __typename
      }
      __typename
    }
    __typename
  }
}
"""

BUYER_FUNDING_CONTEXT_QUERY = """
query BuyerFundingContextQuery($token: String!) {
  checkoutSession(token: $token) {
    buyer {
      userId
      auth {
        accessToken
        __typename
      }
      __typename
    }
    fundingOptions {
      fundingInstrument {
        id
        lastDigits
        type
        __typename
      }
      allPlans {
        fundingSources {
          fundingInstrument {
            id
            type
            __typename
          }
          __typename
        }
        __typename
      }
      __typename
    }
    __typename
  }
}
"""

# Current checkoutweb/weasley CheckoutSessionDataQuery.  The old
# isChangePaymentMethodFlow flag no longer exists on CheckoutSessionFlags and
# causes GRAPHQL_VALIDATION_FAILED.
CHECKOUT_SESSION_DATA_QUERY = """
query CheckoutSessionDataQuery($token: String!) {
  checkoutSession(token: $token) {
    allowedCardIssuers
    cart {
      soldoutUrl
      showSoldoutPage
      amounts {
        total {
          currencyCode
          currencyValue
          __typename
        }
        __typename
      }
      billingAddress {
        city
        country
        line1
        line2
        postalCode
        state
        formattedFullAddress
        __typename
      }
      cancelUrl {
        href
        __typename
      }
      description
      email {
        stringValue
        __typename
      }
      intent
      noteToBuyer
      payer {
        name {
          familyName
          givenName
          __typename
        }
        __typename
      }
      formattedPhoneNumber(shouldValidate: true, useInternationalFormat: true)
      phoneNumber(shouldValidate: true, stripDialingCode: true)
      shippingAddress {
        city
        country
        firstName
        isStoreAddress
        lastName
        line1
        line2
        postalCode
        state
        formattedFullAddress
        __typename
      }
      shippingMethods {
        amount {
          currencyCode
          currencyValue
          __typename
        }
        id
        label
        selected
        type
        __typename
      }
      __typename
    }
    checkoutSessionType
    merchant {
      country
      merchantId
      name
      __typename
    }
    __typename
  }
}
"""

# Current checkoutweb/weasley GriffinMetadataQuery.  The old root field
# `griffin(token: ...)` was removed; locale metadata now lives under
# Query.localeMetadata.
GRIFFIN_METADATA_QUERY = """
query GriffinMetadataQuery($countryCode: CountryCodes!, $languageCode: CheckoutContentLanguageCode!, $shippingCountryCode: CountryCodes!) {
  localeMetadata {
    address(countryCode: $countryCode, languageCode: $languageCode) {
      layout {
        name
        isRequired
        maxLength
        minLength
        regex
        __typename
      }
      __typename
    }
    shippingAddress: address(countryCode: $shippingCountryCode, languageCode: $languageCode) {
      layout {
        name
        isRequired
        maxLength
        minLength
        regex
        __typename
      }
      __typename
    }
    currencyCode(countryCode: $countryCode)
    date(countryCode: $countryCode, languageCode: $languageCode) {
      displayFormat
      datePattern
      __typename
    }
    phone(countryCode: $countryCode) {
      masks {
        mobile
        __typename
      }
      patterns {
        default
        __typename
      }
      __typename
    }
    territories(countryCode: $countryCode, languageCode: $languageCode) {
      code
      internationalDialingCode
      name
      region
      suggestedDefaultLanguage
      __typename
    }
    __typename
  }
}
"""

SUPPORTED_FUNDING_SOURCES_QUERY = """
query SupportedFundingSourcesQuery($token: String!, $userCountry: CountryCodes) {
  checkoutSession(token: $token) {
    supportedFundingSources(userCountry: $userCountry) {
      issuers {
        name
        usage
        issuerLogoUrl {
          href
          __typename
        }
        rank
        __typename
      }
      __typename
    }
    __typename
  }
}
"""

INSTALLMENT_OPTIONS_QUERY = """
query InstallmentOptionsQuery($buyerCountry: CountryCodes!, $cardNumber: String!, $cardType: CardIssuerType, $token: String!) {
  getInstallmentsForOnboardingFlows(
    buyerCountry: $buyerCountry
    cardNumber: $cardNumber
    cardType: $cardType
    token: $token
  ) {
    discount {
      amount {
        currencyCode
        currencyFormat
        currencyFormatSymbolISOCurrency
        currencySymbol
        currencyValue
        __typename
      }
      percentage
      __typename
    }
    monthlyPayment {
      currencyCode
      currencyFormat
      currencyFormatSymbolISOCurrency
      currencySymbol
      currencyValue
      __typename
    }
    term
    totalCost {
      currencyCode
      currencyFormat
      currencyFormatSymbolISOCurrency
      currencySymbol
      currencyValue
      __typename
    }
    totalConsumerFee {
      amount {
        currencyCode
        currencyFormat
        currencyFormatSymbolISOCurrency
        currencySymbol
        currencyValue
        __typename
      }
      percentage
      __typename
    }
    feeReferenceId
    __typename
  }
}
"""

ADDRESS_AUTOCOMPLETE_FROM_POSTAL_CODE_QUERY = """
query AddressAutocompleteFromPostalCodeQuery($postalCode: String!, $token: String!, $country: CountryCodes) {
  addressNormalization(
    postalCode: $postalCode
    token: $token
    processMode: FASTCOMPLETION
    scope: STREET_LEVEL
    country: $country
  ) {
    line1
    line2
    city
    state
    postalCode
    __typename
  }
}
"""

# Browser Weasley calls this early on signup.  The otpLoginContext payload is
# not required by this lightweight flow, but the call warms the session and
# keeps the GraphQL sequence closer to the captured browser trace.
DEFERRED_FEATURE_QUERY = """
query DeferredFeature($channel: String!, $countryCodeAsString: String!, $isBaslAsString: String!, $isForcedGuest: String!, $token: String!, $integrationType: String!) {
  otpLoginContext(token: $token, integrationType: $integrationType) {
    __typename
    context
  }
  elmoExperiment(
    app: "checkoutuinodeweb"
    filters: [{key: "Country", value: $countryCodeAsString}, {key: "Channel", value: $channel}, {key: "IsBasl", value: $isBaslAsString}, {key: "IsGuestOnly", value: $isForcedGuest}]
    res: "weasley:deferredFeature:memberAsDefault"
  ) {
    __typename
    treatments {
      __typename
      experimentId
      experimentName
      factors {
        __typename
        key
        value
      }
      treatmentId
      treatmentName
    }
  }
}
"""

# Step 1: Send SMS OTP
INITIATE_2FA_PHONE_MUTATION = """
mutation InitiateRiskBasedTwoFactorPhoneConfirmationMutation($phoneNumber: String!, $locale: LocaleInput!, $phoneCountry: CountryCodes!, $token: String!) {
  initiateRiskBasedTwoFactorPhoneConfirmation(
    locale: $locale
    phoneCountry: $phoneCountry
    phoneNumber: $phoneNumber
    token: $token
  ) {
    authId
    challengeId
    state
    __typename
  }
}
"""

# Step 2: Verify SMS OTP pin
CONFIRM_2FA_PHONE_MUTATION = """
mutation ConfirmRiskBasedTwoFactorPhoneConfirmationMutation($pin: String!, $authId: String!, $challengeId: String!, $token: String!) {
  confirmRiskBasedTwoFactorPhoneConfirmation(
    pin: $pin
    authId: $authId
    challengeId: $challengeId
    token: $token
  ) {
    authId
    challengeId
    state
    __typename
  }
}
"""

# Step 3: Create account + add card + onboard
SIGNUP_NEW_MEMBER_MUTATION = """
mutation SignUpNewMemberMutation($bank: BankAccountInput, $billingAddress: AddressInput, $card: CardInput, $contentIdentifier: String, $country: CountryCodes, $countrySpecificFirstName: String, $countrySpecificLastName: String, $crsData: CommonReportingStandardsInput, $currencyConversionType: CheckoutCurrencyConversionType, $dateOfBirth: DateOfBirth, $email: String!, $firstName: String!, $gender: Gender, $identityDocument: IdentityDocumentInput, $lastName: String!, $middleName: String, $marketingOptOut: Boolean, $nationality: CountryCodes, $occupation: Occupation, $password: String, $phone: PhoneInput!, $placeOfBirth: CountryCodes, $secondaryIdentityDocument: IdentityDocumentInput, $selectedInstallmentOption: InstallmentsInput, $shareAddressWithDonatee: Boolean, $shippingAddress: AddressInput, $supportedThreeDsExperiences: [ThreeDSPaymentExperience], $token: String!, $residentialAddress: AddressInput, $isSignupIncentiveOptIn: Boolean, $isSignupIncentiveOptInStretch: Boolean, $legalAgreements: LegalAgreementsInput, $collectedConsents: [CollectedConsent]) {
  onboardAccount: signUpNewMember(
    bank: $bank
    billingAddress: $billingAddress
    card: $card
    contentIdentifier: $contentIdentifier
    countrySpecificFirstName: $countrySpecificFirstName
    countrySpecificLastName: $countrySpecificLastName
    country: $country
    crsData: $crsData
    currencyConversionType: $currencyConversionType
    dateOfBirth: $dateOfBirth
    email: $email
    firstName: $firstName
    gender: $gender
    identityDocument: $identityDocument
    lastName: $lastName
    middleName: $middleName
    marketingOptOut: $marketingOptOut
    nationality: $nationality
    occupation: $occupation
    password: $password
    phone: $phone
    placeOfBirth: $placeOfBirth
    secondaryIdentityDocument: $secondaryIdentityDocument
    selectedInstallmentOption: $selectedInstallmentOption
    shareAddressWithDonatee: $shareAddressWithDonatee
    shippingAddress: $shippingAddress
    token: $token
    residentialAddress: $residentialAddress
    isSignupIncentiveOptIn: $isSignupIncentiveOptIn
    isSignupIncentiveOptInStretch: $isSignupIncentiveOptInStretch
    legalAgreements: $legalAgreements
    collectedConsents: $collectedConsents
  ) {
    ...buyer
    flags {
      is3DSecureRequired
      __typename
    }
    ...fundingOptions
    paymentContingencies {
      ...threeDomainSecure
      ...threeDSContingencyData
      __typename
    }
    __typename
  }
}

fragment buyer on CheckoutSession {
  buyer {
    auth {
      accessToken
      __typename
    }
    userId
    __typename
  }
  __typename
}

fragment fundingOptions on CheckoutSession {
  fundingOptions {
    allPlans {
      fundingSources {
        fundingInstrument {
          id
          __typename
        }
        amount {
          currencyCode
          currencyValue
          __typename
        }
        __typename
      }
      fundingContingencies {
        ... on OpenBankingContingency {
          encryptedId
          contingencyReasons
          contingencyType
          __typename
        }
        __typename
      }
      __typename
    }
    fundingInstrument {
      id
      lastDigits
      name
      nameDescription
      type
      __typename
    }
    __typename
  }
  __typename
}

fragment threeDomainSecure on PaymentContingencies {
  threeDomainSecure(experiences: $supportedThreeDsExperiences) {
    status
    redirectUrl {
      href
      __typename
    }
    method
    parameter
    experience
    requestParams {
      key
      value
      __typename
    }
    __typename
  }
  __typename
}

fragment threeDSContingencyData on PaymentContingencies {
  threeDSContingencyData {
    name
    causeName
    resolution {
      type
      resolutionName
      paymentCard {
        billingAddress {
          line1
          line2
          city
          state
          country
          postalCode
          __typename
        }
        expireYear
        expireMonth
        currencyCode
        cardProductClass
        id
        encryptedNumber
        type
        number
        bankIdentificationNumber
        __typename
      }
      contingencyContext {
        deviceDataCollectionUrl {
          href
          __typename
        }
        jwtSpecification {
          jwtDuration
          jwtIssuer
          jwtOrgUnitId
          type
          __typename
        }
        authenticationProvider
        cardBrandProcessed
        reason
        referenceId
        source
        __typename
      }
      __typename
    }
    __typename
  }
  __typename
}
"""

# Step 4: Final billing agreement authorization
AUTHORIZE_BILLING_MUTATION = """
mutation authorize($billingAgreementId: String!, $addressId: String, $fundingPreference: billingFundingPreferenceInput, $legalAgreements: billingLegalAgreementsInput) {
  billing {
    authorize(
      billingAgreementId: $billingAgreementId
      addressId: $addressId
      fundingPreference: $fundingPreference
      legalAgreements: $legalAgreements
    ) {
      billingAgreementToken
      paymentAction
      returnURL {
        href
        __typename
      }
      buyer {
        userId
        __typename
      }
      __typename
    }
    __typename
  }
}
"""
